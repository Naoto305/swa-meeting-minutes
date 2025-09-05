import azure.functions as func
import logging
import json
import os
import requests
import uuid
import base64
import urllib.parse
from datetime import datetime, timedelta
from azure.storage.blob import (
    BlobServiceClient,
    generate_blob_sas,
    BlobSasPermissions
)
from openai import AzureOpenAI

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
        # Decode client principal to capture user identity
        user_id = None
        user_details = None
        try:
            principal_json = base64.b64decode(auth_header).decode('utf-8')
            principal = json.loads(principal_json)
            user_id = principal.get('userId')
            user_details = principal.get('userDetails')
        except Exception as e:
            logging.warning(f"Failed to parse client principal: {e}")
        
        # --- Get File and Prompt ---
        file = req.files.get('file')
        prompt = req.form.get('prompt', '議事録を日本語で要約してください。')
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

        # Base64 encode metadata values to handle non-ASCII characters
        prompt_b64 = base64.b64encode(prompt.encode('utf-8')).decode('ascii')
        filename_b64 = base64.b64encode(original_filename.encode('utf-8')).decode('ascii')

        metadata = {
            "original_prompt_b64": prompt_b64,
            "original_filename_b64": filename_b64
        }
        if user_id:
            metadata["user_id"] = user_id
        if user_details:
            metadata["user_details_b64"] = base64.b64encode(user_details.encode('utf-8')).decode('ascii')
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

                # --- Generate SAS token for the source blob ---
                connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
                blob_service_client = BlobServiceClient.from_connection_string(connect_str)
                
                sas_token = generate_blob_sas(
                    account_name=blob_service_client.account_name,
                    container_name=AUDIO_CONTAINER,
                    blob_name=source_blob_name,
                    account_key=blob_service_client.credential.account_key,
                    permission=BlobSasPermissions(read=True),
                    expiry=datetime.utcnow() + timedelta(hours=1) # 1-hour validity
                )
                
                blob_url_with_sas = f"{blob_url}?{sas_token}"
                logging.info(f"Generated SAS URL for source blob: {blob_url_with_sas}")

                # --- Call Speech Service ---
                speech_api_key = os.environ.get("SPEECH_KEY")
                speech_endpoint = os.environ.get("SPEECH_ENDPOINT")
                if not all([speech_api_key, speech_endpoint]):
                    logging.error("Speech service credentials are not configured.")
                    return func.HttpResponse("Server configuration error.", status_code=500)

                destination_container_url = os.environ.get("TRANSCRIPTION_DESTINATION_CONTAINER_SAS_URL")
                if not destination_container_url:
                    logging.error("Destination container SAS URL is not configured.")
                    return func.HttpResponse("Server configuration error.", status_code=500)

                transcription_endpoint = f"{speech_endpoint.rstrip('/')}/speechtotext/v3.1/transcriptions"

                payload = {
                    "contentUrls": [blob_url_with_sas],
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
            event_type = event.get('eventType')
            
            if event_type == 'Microsoft.EventGrid.SubscriptionValidationEvent':
                validation_code = event['data']['validationCode']
                logging.info(f"Got validation event for generate. Responding with code: {validation_code}")
                return func.HttpResponse(json.dumps({"validationResponse": validation_code}), mimetype="application/json")

            if event_type == 'Microsoft.Storage.BlobCreated':
                transcript_blob_url = event['data']['url']
                logging.info(f"Processing transcript blob: {transcript_blob_url}")

                # Correctly parse the full blob name (including virtual directories) from the URL
                blob_path = urllib.parse.urlparse(transcript_blob_url).path
                transcript_blob_name = blob_path.split(f"/{TRANSCRIPTS_CONTAINER}/", 1)[1]

                # Only process the detailed transcription result file
                if not os.path.basename(transcript_blob_name).startswith('contenturl_'):
                    logging.info("Skipping blob as it is not a detailed transcript file.")
                    continue

                # --- Read the transcription result from the TRANSCRIPTS storage account ---
                transcripts_connect_str = os.getenv('TRANSCRIPTS_STORAGE_CONNECTION_STRING')
                transcripts_blob_service_client = BlobServiceClient.from_connection_string(transcripts_connect_str)
                
                transcript_blob_client = transcripts_blob_service_client.get_blob_client(
                    container=TRANSCRIPTS_CONTAINER, 
                    blob=transcript_blob_name
                )
                transcript_data = transcript_blob_client.download_blob().readall()
                transcript_json = json.loads(transcript_data)

                transcript_text = transcript_json['combinedRecognizedPhrases'][0]['display']
                original_audio_url = transcript_json['source']

                # --- Get metadata from original audio blob from the AUDIO storage account ---
                audio_connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
                audio_blob_service_client = BlobServiceClient.from_connection_string(audio_connect_str)

                parsed_url = urllib.parse.urlparse(original_audio_url)
                original_audio_blob_name = os.path.basename(parsed_url.path)
                
                audio_blob_client = audio_blob_service_client.get_blob_client(container=AUDIO_CONTAINER, blob=original_audio_blob_name)
                audio_metadata = audio_blob_client.get_blob_properties().metadata

                # --- Decode metadata ---
                user_prompt = base64.b64decode(audio_metadata['original_prompt_b64']).decode('utf-8')
                original_filename = base64.b64decode(audio_metadata['original_filename_b64']).decode('utf-8')

                logging.info(f"Successfully retrieved transcript and metadata for {original_filename}.")

                # --- Call Azure OpenAI ---
                openai_client = AzureOpenAI(
                    azure_endpoint=os.environ.get("OPENAI_API_BASE"),
                    api_key=os.environ.get("OPENAI_API_KEY"),
                    api_version="2024-02-01" # Use a recent, stable version
                )

                system_prompt = "You are an expert assistant skilled at creating concise and accurate meeting minutes from a transcription. Structure the output clearly with headings like 'Summary', 'Action Items', and 'Decisions'."
                final_prompt = f"Please create meeting minutes based on the following transcription and user prompt.\n\nUser Prompt: {user_prompt}\n\nTranscription:\n\n{transcript_text}"

                response = openai_client.chat.completions.create(
                    model=os.environ.get("OPENAI_DEPLOYMENT_NAME"),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": final_prompt}
                    ]
                )
                
                generated_minutes = response.choices[0].message.content
                logging.info("Successfully generated minutes from OpenAI.")

                # --- Save the final minutes to the MINUTES storage account ---
                minutes_connect_str = os.getenv('MINUTES_STORAGE_CONNECTION_STRING')
                minutes_blob_service_client = BlobServiceClient.from_connection_string(minutes_connect_str)

                minutes_base_name, _ = os.path.splitext(original_filename)
                audio_base, _ = os.path.splitext(original_audio_blob_name)
                minutes_filename = f"{audio_base}_minutes.txt"
                
                minutes_blob_client = minutes_blob_service_client.get_blob_client(container=MINUTES_CONTAINER, blob=minutes_filename)
                # Propagate identity and context metadata from the original audio blob
                minutes_metadata = {}
                for key in [
                    'user_id', 'user_details_b64', 'original_prompt_b64', 'original_filename_b64'
                ]:
                    if key in audio_metadata:
                        minutes_metadata[key] = audio_metadata[key]
                minutes_blob_client.upload_blob(generated_minutes.encode('utf-8'), overwrite=True, metadata=minutes_metadata)
                logging.info(f"Successfully uploaded final minutes to {minutes_filename} in container {MINUTES_CONTAINER}.")

        return func.HttpResponse("Generation event processed.", status_code=200)

    except Exception as e:
        logging.error(f"An error occurred in generate_eventgrid_trigger: {e}", exc_info=True)
        return func.HttpResponse("An error occurred while processing the generation event.", status_code=500)



@app.route(route="list-minutes")
def list_minutes(req: func.HttpRequest) -> func.HttpResponse:
    """List generated minutes files from Storage Account 3.

    Returns JSON array with name, last_modified, and job_id derived from filename.
    """
    try:
        minutes_connect_str = os.getenv('MINUTES_STORAGE_CONNECTION_STRING')
        if not minutes_connect_str:
            logging.error("MINUTES_STORAGE_CONNECTION_STRING is not configured.")
            return func.HttpResponse("Server configuration error.", status_code=500)

        # Extract current user from Easy Auth header for filtering
        auth_header = req.headers.get('X-MS-CLIENT-PRINCIPAL')
        current_user_id = None
        if auth_header:
            try:
                principal_json = base64.b64decode(auth_header).decode('utf-8')
                principal = json.loads(principal_json)
                current_user_id = principal.get('userId')
            except Exception as e:
                logging.warning(f"Failed to parse client principal in list-minutes: {e}")

        blob_service = BlobServiceClient.from_connection_string(minutes_connect_str)
        container = blob_service.get_container_client(MINUTES_CONTAINER)

        items = []
        # Include metadata so we can filter by owner without per-blob requests
        for b in container.list_blobs(include=['metadata']):
            # Only include text files that look like generated minutes
            name = b.name
            if not name.lower().endswith('.txt'):
                continue
            # If user filtering is enabled, skip blobs that don't belong to the current user
            if current_user_id:
                owner = None
                try:
                    owner = (b.metadata or {}).get('user_id') if hasattr(b, 'metadata') else None
                except Exception:
                    owner = None
                if owner and owner != current_user_id:
                    continue
            job_id = name[:-12] if name.endswith('_minutes.txt') and len(name) > len('_minutes.txt') else os.path.splitext(name)[0]
            last_modified = None
            try:
                # Some SDK versions include last_modified on the list response; fall back if missing
                if getattr(b, 'last_modified', None):
                    last_modified = b.last_modified.isoformat()
                else:
                    props = container.get_blob_client(name).get_blob_properties()
                    last_modified = props.last_modified.isoformat() if props and getattr(props, 'last_modified', None) else None
            except Exception as e:
                logging.warning(f"Failed to get last_modified for {name}: {e}")

            items.append({
                'name': name,
                'last_modified': last_modified,
                'job_id': job_id
            })

        # Sort by last_modified desc if available
        items.sort(key=lambda x: x['last_modified'] or '', reverse=True)

        return func.HttpResponse(
            json.dumps({'minutes': items}, ensure_ascii=False),
            mimetype="application/json",
            status_code=200
        )

    except Exception as e:
        logging.error(f"Error in list_minutes: {e}", exc_info=True)
        return func.HttpResponse("Failed to list minutes.", status_code=500)


@app.route(route="status")
def status(req: func.HttpRequest) -> func.HttpResponse:
    """Return status and content for a minutes job.

    Query params:
      - job_id: base name used when creating minutes (audio blob base)
      - name: optional, exact minutes blob name (overrides job_id)
    """
    try:
        job_id = req.params.get('job_id')
        name = req.params.get('name')
        if not job_id and not name:
            return func.HttpResponse("Missing job_id or name.", status_code=400)

        # Determine blob name
        if name:
            blob_name = name
        else:
            blob_name = f"{job_id}_minutes.txt"

        minutes_connect_str = os.getenv('MINUTES_STORAGE_CONNECTION_STRING')
        if not minutes_connect_str:
            logging.error("MINUTES_STORAGE_CONNECTION_STRING is not configured.")
            return func.HttpResponse("Server configuration error.", status_code=500)

        blob_service = BlobServiceClient.from_connection_string(minutes_connect_str)
        blob_client = blob_service.get_blob_client(container=MINUTES_CONTAINER, blob=blob_name)

        if not blob_client.exists():
            # Not found yet -> pending
            return func.HttpResponse(
                json.dumps({'status': 'pending', 'job_id': job_id or name}, ensure_ascii=False),
                mimetype="application/json",
                status_code=404
            )

        data = blob_client.download_blob().readall()
        minutes_text = data.decode('utf-8', errors='replace') if isinstance(data, (bytes, bytearray)) else str(data)

        return func.HttpResponse(
            json.dumps({'status': 'completed', 'job_id': job_id or name, 'minutes': minutes_text}, ensure_ascii=False),
            mimetype="application/json",
            status_code=200
        )

    except Exception as e:
        logging.error(f"Error in status endpoint: {e}", exc_info=True)
        return func.HttpResponse("Failed to get status.", status_code=500)


