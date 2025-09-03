import azure.functions as func
import logging
import json
import os
import requests
import uuid
from azure.storage.blob import (
    BlobServiceClient
)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Define container names
AUDIO_CONTAINER = "audio"
TRANSCRIPTS_CONTAINER = "transcripts"
MINUTES_CONTAINER = "minutes"

@app.route(route="upload")
def upload_http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed an upload request.')

    try:
        # --- Authentication ---
        auth_header = req.headers.get('X-MS-CLIENT-PRINCIPAL')
        if not auth_header:
            return func.HttpResponse("Unauthorized: User not authenticated.", status_code=401)
        
        # --- Get File and Prompt ---
        file = req.files.get('file')
        prompt = req.form.get('prompt', '議事録を要約してください。')
        if not file:
            return func.HttpResponse("Please provide an audio file in the request.", status_code=400)

        # --- Upload to Azure Storage with Metadata ---
        original_filename = file.filename
        # Create a unique name for the blob to avoid overwrites
        base_name, extension = os.path.splitext(original_filename)
        audio_filename = f"{base_name}_{uuid.uuid4()}{extension}"

        connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        
        blob_client = blob_service_client.get_blob_client(container=AUDIO_CONTAINER, blob=audio_filename)
        
        logging.info(f"Uploading {audio_filename} to container {AUDIO_CONTAINER}.")
        # Get file content from the stream
        file_content = file.read()
        blob_client.upload_blob(file_content, overwrite=True)

        # Save prompt and original filename in metadata
        metadata = {
            "original_prompt": prompt,
            "original_filename": original_filename
        }
        blob_client.set_blob_metadata(metadata)
        logging.info(f"Metadata set for {audio_filename}.")

        return func.HttpResponse(
            json.dumps({'message': f'Audio file {original_filename} uploaded as {audio_filename}. Transcription will begin shortly.'}),
            mimetype="application/json",
            status_code=202 # Accepted
        )

    except Exception as e:
        logging.error(f"Error in upload_http_trigger: {e}")
        return func.HttpResponse("An error occurred while processing the request.", status_code=500)


@app.route(route="transcribe")
def transcribe_eventgrid_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python Event Grid trigger function processed a request for transcription.')

    try:
        events = req.get_json()
        for event in events:
            event_type = event.get('eventType')
            
            if event_type == 'Microsoft.EventGrid.SubscriptionValidationEvent':
                validation_code = event['data']['validationCode']
                logging.info(f"Got validation event. Responding with code: {validation_code}")
                return func.HttpResponse(json.dumps({"validationResponse": validation_code}), mimetype="application/json")

            if event_type == 'Microsoft.Storage.BlobCreated':
                logging.info(f"Processing BlobCreated event for transcription: {event['id']}")
                
                blob_url = event['data']['url']
                source_blob_name = os.path.basename(blob_url)

                speech_api_key = os.environ.get("SPEECH_KEY")
                speech_endpoint = os.environ.get("SPEECH_ENDPOINT")
                if not all([speech_api_key, speech_endpoint]):
                    logging.error("Speech service credentials are not configured.")
                    return func.HttpResponse("Server configuration error.", status_code=500)

                destination_container_url = os.environ.get("TRANSCRIPTION_DESTINATION_CONTAINER_SAS_URL")
                if not destination_container_url:
                    logging.error("Destination container SAS URL is not configured.")
                    return func.HttpResponse("Server configuration error.", status_code=500)

                transcription_endpoint = f"{speech_endpoint}/speechtotext/v3.1/transcriptions"

                payload = {
                    "contentUrls": [blob_url],
                    "locale": "ja-JP",
                    "displayName": f"transcription-{source_blob_name}",
                    "properties": {
                        "diarizationEnabled": True,
                        "wordLevelTimestampsEnabled": True,
                        "destinationContainerUrl": destination_container_url
                    },
                }
                
                headers = {
                    "Ocp-Apim-Subscription-Key": speech_api_key,
                    "Content-Type": "application/json"
                }

                response = requests.post(transcription_endpoint, headers=headers, data=json.dumps(payload))
                response.raise_for_status()

                logging.info(f"Successfully submitted transcription request for {source_blob_name}. Location: {response.headers.get('Location')}")

        return func.HttpResponse("Event processed.", status_code=200)

    except requests.exceptions.RequestException as re:
        logging.error(f"Request to downstream service failed: {re}")
        if re.response is not None:
            logging.error(f"Response body: {re.response.text}")
        return func.HttpResponse("Failed to communicate with speech service.", status_code=500)
    except Exception as e:
        logging.error(f"An error occurred in transcribe_eventgrid_trigger: {e}")
        return func.HttpResponse("An error occurred while processing the event.", status_code=500)


@app.route(route="generate")
def generate_eventgrid_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python Event Grid trigger function processed a request for generation.')
    
    try:
        events = req.get_json()
        for event in events:
            if event.get('eventType') == 'Microsoft.EventGrid.SubscriptionValidationEvent':
                validation_code = event['data']['validationCode']
                logging.info(f"Got validation event for generate. Responding with code: {validation_code}")
                return func.HttpResponse(json.dumps({"validationResponse": validation_code}), mimetype="application/json")
            else:
                logging.info(f"Received event for generation: {event.get('eventType')}")

    except Exception as e:
        logging.error(f"Error in generate_eventgrid_trigger: {e}")

    return func.HttpResponse("Generation endpoint is active but not fully implemented.", status_code=200)
