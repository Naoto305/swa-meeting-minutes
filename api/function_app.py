import azure.functions as func
import logging
import json
import os
import requests
from datetime import datetime, timedelta
from azure.storage.blob import (
    BlobServiceClient,
    generate_container_sas,
    ContainerSasPermissions,
    generate_blob_sas,
    BlobSasPermissions
)

app = func.FunctionApp()

# (UploadFile function is omitted for brevity but remains in the file)
@app.route(route="upload", auth_level=func.AuthLevel.ANONYMOUS)
def UploadFile(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request.')

    try:
        auth_header = req.headers.get('X-MS-CLIENT-PRINCIPAL')
        if not auth_header:
            return func.HttpResponse(
                "Unauthorized: User not authenticated.",
                status_code=401
            )

        file = req.files.get('file')
        if not file:
            return func.HttpResponse(
                "Please provide a file in the request.",
                status_code=400
            )

        connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        container_name = "uploads"
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)

        try:
            blob_service_client.create_container(container_name)
        except Exception as e:
            logging.info(f"Container {container_name} already exists.")

        blob_client = blob_service_client.get_blob_client(container=container_name, blob=file.filename)
        blob_client.upload_blob(file, overwrite=True)

        logging.info(f"File {file.filename} uploaded to {container_name}.")

        return func.HttpResponse(
            json.dumps({'message': f'File {file.filename} uploaded successfully.'}),
            mimetype="application/json",
            status_code=200
        )

    except Exception as e:
        logging.error(f"Error: {e}")
        return func.HttpResponse(
             "An error occurred while processing the request.",
             status_code=500
        )


@app.route("RequestTranscription", auth_level=func.AuthLevel.ANONYMOUS)
def RequestTranscription(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request for Event Grid.')

    try:
        events = req.get_json()
        for event in events:
            event_type = event.get('eventType')
            
            if event_type == 'Microsoft.EventGrid.SubscriptionValidationEvent':
                validation_code = event['data']['validationCode']
                logging.info(f"Got validation event. Responding with code: {validation_code}")
                return func.HttpResponse(json.dumps({"validationResponse": validation_code}), mimetype="application/json")

            if event_type == 'Microsoft.Storage.BlobCreated':
                logging.info(f"Processing BlobCreated event: {event}")
                blob_url = event['data']['url']
                
                # subjectからblob名を正しく抽出
                source_blob_name = event['subject'].split('/blobs/')[-1]

                speech_api_key = os.environ.get("SPEECH_SERVICE_KEY")
                speech_region = os.environ.get("SPEECH_SERVICE_REGION")
                endpoint = f"https://{speech_region}.api.cognitive.microsoft.com/speechtotext/v3.1/transcriptions"

                # AI Speechで読み取るために、入力ファイルのSASトークン付きURLを生成
                source_storage_conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
                source_container_name = "uploads"
                
                source_blob_service_client = BlobServiceClient.from_connection_string(source_storage_conn_str)
                
                source_sas_token = generate_blob_sas(
                    account_name=source_blob_service_client.account_name,
                    container_name=source_container_name,
                    blob_name=source_blob_name,
                    account_key=source_blob_service_client.credential.account_key,
                    permission=BlobSasPermissions(read=True),
                    expiry=datetime.utcnow() + timedelta(hours=24)
                )
                source_url_with_sas = f"{blob_url}?{source_sas_token}"

                # 文字起こし結果を保存するコンテナーのURLとSASトークンを生成
                transcript_storage_conn_str = os.environ.get("TRANSCRIPTION_STORAGE_CONNECTION_STRING")
                transcript_container_name = "transcripts"
                
                transcript_blob_service_client = BlobServiceClient.from_connection_string(transcript_storage_conn_str)
                
                try:
                    transcript_blob_service_client.create_container(transcript_container_name)
                except Exception as e:
                    logging.info(f"Container {transcript_container_name} already exists.")

                transcript_sas_token = generate_container_sas(
                    account_name=transcript_blob_service_client.account_name,
                    container_name=transcript_container_name,
                    account_key=transcript_blob_service_client.credential.account_key,
                    permission=ContainerSasPermissions(write=True, list=True),
                    expiry=datetime.utcnow() + timedelta(hours=24)
                )
                destination_container_url = f"{transcript_blob_service_client.url}{transcript_container_name}?{transcript_sas_token}"

                # AI Speechへのリクエストボディ (localeを再追加)
                payload = {
                    "contentUrls": [source_url_with_sas],
                    "locale": "ja-JP",
                    "displayName": f"transcription-{source_blob_name}",
                    "properties": {
                        "diarizationEnabled": True,
                        "wordLevelTimestampsEnabled": True,
                        "destinationContainerUrl": destination_container_url,
                        "languageIdentification": {
                            "candidateLocales": ["ja-JP", "en-US"],
                        },
                    },
                }

                headers = {
                    "Ocp-Apim-Subscription-Key": speech_api_key,
                    "Content-Type": "application/json"
                }
                response = requests.post(endpoint, headers=headers, data=json.dumps(payload))
                response.raise_for_status()

                logging.info(f"Successfully submitted transcription request for {source_blob_name}.")

        return func.HttpResponse("Event processed.", status_code=200)

    except Exception as e:
        if hasattr(e, 'response') and e.response is not None:
             logging.error(f"Request to downstream service failed with status code {e.response.status_code} and response body: {e.response.text}")
        logging.error(f"An error occurred: {e}")
        return func.HttpResponse("An error occurred while processing the event.", status_code=500)