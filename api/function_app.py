import azure.functions as func
import logging
import json
import os
import requests
import ffmpeg
import tempfile
import uuid
from datetime import datetime, timedelta
from azure.storage.blob import (
    BlobServiceClient,
    generate_container_sas,
    ContainerSasPermissions,
    generate_blob_sas,
    BlobSasPermissions
)
import openai

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Define container names
AUDIO_CONTAINER = "audio"
TRANSCRIPTS_CONTAINER = "transcripts"
MINUTES_CONTAINER = "minutes"

@app.route(route="upload")
def upload_http_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed an upload request.')

    try:
        # --- Authentication (remains the same) ---
        auth_header = req.headers.get('X-MS-CLIENT-PRINCIPAL')
        if not auth_header:
            return func.HttpResponse("Unauthorized: User not authenticated.", status_code=401)
        
        # --- Get File and Prompt ---
        file = req.files.get('file')
        prompt = req.form.get('prompt', '議事録を要約してください。') # Default prompt
        if not file:
            return func.HttpResponse("Please provide a file in the request.", status_code=400)

        # --- Audio Extraction ---
        original_filename = file.filename
        temp_dir = tempfile.gettempdir()
        input_path = os.path.join(temp_dir, original_filename)
        
        # Save uploaded file temporarily
        with open(input_path, "wb") as f:
            f.write(file.read())
        
        # Prepare output path for audio
        base_name, _ = os.path.splitext(original_filename)
        audio_filename = f"{base_name}_{uuid.uuid4()}.mp3"
        output_path = os.path.join(temp_dir, audio_filename)

        logging.info(f"Extracting audio from {original_filename} to {audio_filename}")
        try:
            ffmpeg.input(input_path).output(output_path, acodec='libmp3lame', audio_bitrate='128k').run(overwrite_output=True, quiet=True)
            logging.info("Audio extraction successful.")
        except ffmpeg.Error as e:
            logging.error(f"FFmpeg error: {e.stderr.decode('utf8') if e.stderr else 'Unknown error'}")
            return func.HttpResponse("Failed to process video/audio file.", status_code=500)

        # --- Upload to Azure Storage with Metadata ---
        connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        blob_service_client = BlobServiceClient.from_connection_string(connect_str)
        
        blob_client = blob_service_client.get_blob_client(container=AUDIO_CONTAINER, blob=audio_filename)
        
        logging.info(f"Uploading {audio_filename} to container {AUDIO_CONTAINER}.")
        with open(output_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)

        # Save prompt and original filename in metadata
        metadata = {
            "original_prompt": prompt,
            "original_filename": original_filename
        }
        blob_client.set_blob_metadata(metadata)
        logging.info(f"Metadata set for {audio_filename}.")

        # --- Cleanup temporary files ---
        os.remove(input_path)
        os.remove(output_path)

        return func.HttpResponse(
            json.dumps({'message': f'File processed and {audio_filename} uploaded successfully. Transcription will begin shortly.'}),
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

                # The destination container URL with SAS must be provided as an environment variable
                destination_container_url = os.environ.get("TRANSCRIPTION_DESTINATION_CONTAINER_SAS_URL")
                if not destination_container_url:
                    logging.error("Destination container SAS URL is not configured.")
                    return func.HttpResponse("Server configuration error.", status_code=500)

                # The batch transcription API endpoint
                transcription_endpoint = f"{speech_endpoint}/speechtotext/v3.1/transcriptions"

                payload = {
                    "contentUrls": [blob_url],
                    "locale": "ja-JP",
                    "displayName": f"transcription-{source_blob_name}",
                    "properties": {
                        "diarizationEnabled": True,
                        "wordLevelTimestampsEnabled": True,
                    },
                }
                
                headers = {
                    "Ocp-Apim-Subscription-Key": speech_api_key,
                    "Content-Type": "application/json"
                }

                # The REST API for creating a transcription requires the destination to be set
                # in the 'links' part of the response, not in the initial payload.
                # We first create the transcription job, then get the 'files' URL from the response
                # and upload the destination there. A simpler way is to use the destinationContainerUrl.
                # Let's add it back to properties.
                payload["properties"]["destinationContainerUrl"] = destination_container_url

                response = requests.post(transcription_endpoint, headers=headers, data=json.dumps(payload))
                response.raise_for_status() # Raise an exception for bad status codes

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

# --- Placeholder for the next function ---
@app.route(route="generate")
def generate_eventgrid_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python Event Grid trigger function processed a request for generation.')
    # This function will be implemented in the next step.
    # It will handle validation and process BlobCreated events from the transcripts container.
    
    # Dummy implementation for now
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
