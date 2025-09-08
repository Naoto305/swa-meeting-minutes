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
                # Save under virtual folder per user if available
                user_id = audio_metadata.get('user_id') if isinstance(audio_metadata, dict) else None
                if user_id:
                    minutes_filename = f"users/{user_id}/{audio_base}_minutes.txt"
                else:
                    minutes_filename = f"{audio_base}_minutes.txt"
                
                minutes_blob_client = minutes_blob_service_client.get_blob_client(container=MINUTES_CONTAINER, blob=minutes_filename)
                # Propagate identity and context metadata from the original audio blob
                minutes_metadata = {}
                for key in [
                    'user_id', 'user_details_b64', 'original_prompt_b64', 'original_filename_b64'
                ]:
                    if key in audio_metadata:
                        minutes_metadata[key] = audio_metadata[key]
                # Save backlink to transcript for future regeneration
                minutes_metadata['transcript_blob_name'] = transcript_blob_name
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
        # List only the current user's folder if available; otherwise list all and filter
        name_prefix = f"users/{current_user_id}/" if current_user_id else None
        iterator = container.list_blobs(name_starts_with=name_prefix, include=['metadata']) if name_prefix else container.list_blobs(include=['metadata'])
        for b in iterator:
            # Only include text files that look like generated minutes
            name = b.name
            if not name.lower().endswith('.txt'):
                continue
            # If user filtering is enabled (no prefix path match), filter by metadata as a fallback
            if current_user_id and name_prefix is None:
                owner = None
                try:
                    owner = (b.metadata or {}).get('user_id') if hasattr(b, 'metadata') else None
                except Exception:
                    owner = None
                if owner and owner != current_user_id:
                    continue
            base = os.path.basename(name)
            job_id = base[:-12] if base.endswith('_minutes.txt') and len(base) > len('_minutes.txt') else os.path.splitext(base)[0]
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
                'name': name,               # full blob path
                'title': base,              # display name only
                'last_modified': last_modified,
                'job_id': job_id            # derived from base filename
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

        # Get current user for access checks and path resolution
        auth_header = req.headers.get('X-MS-CLIENT-PRINCIPAL')
        current_user_id = None
        if auth_header:
            try:
                principal_json = base64.b64decode(auth_header).decode('utf-8')
                principal = json.loads(principal_json)
                current_user_id = principal.get('userId')
            except Exception as e:
                logging.warning(f"Failed to parse client principal in status: {e}")

        # Determine blob name
        if name:
            blob_name = name
        else:
            # If only job_id is provided, resolve to the current user's folder if available
            if current_user_id:
                blob_name = f"users/{current_user_id}/{job_id}_minutes.txt"
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

        # Access check similar to get-minutes
        try:
            if current_user_id:
                if blob_name.startswith(f"users/{current_user_id}/") is False and blob_name.startswith("users/"):
                    return func.HttpResponse(
                        json.dumps({'status': 'forbidden'}, ensure_ascii=False),
                        mimetype="application/json",
                        status_code=403
                    )
                props = blob_client.get_blob_properties()
                owner = (props.metadata or {}).get('user_id')
                if owner and owner != current_user_id:
                    return func.HttpResponse(
                        json.dumps({'status': 'forbidden'}, ensure_ascii=False),
                        mimetype="application/json",
                        status_code=403
                    )
        except Exception as e:
            logging.warning(f"Access check failed in status: {e}")

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


@app.route(route="get-minutes")
def get_minutes(req: func.HttpRequest) -> func.HttpResponse:
    """Download minutes blob by exact name with user access checks."""
    try:
        name = req.params.get('name')
        if not name:
            return func.HttpResponse("Missing name.", status_code=400)

        # Current user
        auth_header = req.headers.get('X-MS-CLIENT-PRINCIPAL')
        current_user_id = None
        if auth_header:
            try:
                principal_json = base64.b64decode(auth_header).decode('utf-8')
                principal = json.loads(principal_json)
                current_user_id = principal.get('userId')
            except Exception as e:
                logging.warning(f"Failed to parse client principal in get-minutes: {e}")

        minutes_connect_str = os.getenv('MINUTES_STORAGE_CONNECTION_STRING')
        blob_service = BlobServiceClient.from_connection_string(minutes_connect_str)
        blob_client = blob_service.get_blob_client(container=MINUTES_CONTAINER, blob=name)

        if not blob_client.exists():
            return func.HttpResponse("Not found.", status_code=404)

        # Access check
        if current_user_id:
            try:
                # If path includes users/{id}/, verify it matches
                if name.startswith(f"users/{current_user_id}/") is False and name.startswith("users/"):
                    return func.HttpResponse("Forbidden.", status_code=403)
                props = blob_client.get_blob_properties()
                owner = (props.metadata or {}).get('user_id')
                if owner and owner != current_user_id:
                    return func.HttpResponse("Forbidden.", status_code=403)
            except Exception as e:
                logging.warning(f"Access check failed in get-minutes: {e}")

        data = blob_client.download_blob().readall()
        filename = os.path.basename(name) or 'minutes.txt'
        # Ensure .txt extension for download
        if not filename.lower().endswith('.txt'):
            filename = f"{filename}.txt"
        # Build Content-Disposition that is ASCII-safe and UTF-8 friendly (RFC 5987)
        try:
            ascii_fallback = ''.join(ch if ord(ch) < 128 else '_' for ch in filename)
            if not ascii_fallback.strip('_'):
                ascii_fallback = 'minutes.txt'
            filename_star = urllib.parse.quote(filename)
            content_disposition = f"attachment; filename={ascii_fallback}; filename*=UTF-8''{filename_star}"
        except Exception:
            content_disposition = "attachment; filename=minutes.txt"

        return func.HttpResponse(
            data,
            mimetype="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": content_disposition
            },
            status_code=200
        )

    except Exception as e:
        logging.error(f"Error in get_minutes: {e}", exc_info=True)
        return func.HttpResponse("Failed to download minutes.", status_code=500)


@app.route(route="regenerate-minutes")
def regenerate_minutes(req: func.HttpRequest) -> func.HttpResponse:
    """Regenerate minutes from a saved transcript with a new prompt.

    Body JSON supports:
      { "transcript_name": "...", "prompt": "..." }
      or { "name": "minutes blob name", "prompt": "..." } where minutes metadata contains transcript_blob_name
    """
    try:
        if req.method and req.method.upper() == 'GET':
            return func.HttpResponse("Use POST with JSON body.", status_code=405)

        body = req.get_json()
        if not isinstance(body, dict):
            return func.HttpResponse("Invalid JSON body.", status_code=400)
        new_prompt = body.get('prompt')
        transcript_name = body.get('transcript_name')
        minutes_name = body.get('name')
        if not new_prompt:
            return func.HttpResponse("Missing prompt.", status_code=400)

        # Current user
        auth_header = req.headers.get('X-MS-CLIENT-PRINCIPAL')
        current_user_id = None
        if auth_header:
            try:
                principal_json = base64.b64decode(auth_header).decode('utf-8')
                principal = json.loads(principal_json)
                current_user_id = principal.get('userId')
            except Exception as e:
                logging.warning(f"Failed to parse client principal in regenerate: {e}")

        minutes_connect_str = os.getenv('MINUTES_STORAGE_CONNECTION_STRING')
        transcripts_connect_str = os.getenv('TRANSCRIPTS_STORAGE_CONNECTION_STRING')
        audio_connect_str = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        if not all([minutes_connect_str, transcripts_connect_str, audio_connect_str]):
            return func.HttpResponse("Server configuration error.", status_code=500)

        minutes_blob_service = BlobServiceClient.from_connection_string(minutes_connect_str)
        transcripts_blob_service = BlobServiceClient.from_connection_string(transcripts_connect_str)
        audio_blob_service = BlobServiceClient.from_connection_string(audio_connect_str)

        # If minutes_name is given, resolve transcript name from metadata
        if minutes_name and not transcript_name:
            m_client = minutes_blob_service.get_blob_client(MINUTES_CONTAINER, minutes_name)
            if not m_client.exists():
                return func.HttpResponse("Minutes not found.", status_code=404)
            m_props = m_client.get_blob_properties()
            # Access check
            if current_user_id:
                if minutes_name.startswith(f"users/{current_user_id}/") is False and minutes_name.startswith("users/"):
                    return func.HttpResponse("Forbidden.", status_code=403)
                owner = (m_props.metadata or {}).get('user_id') if m_props and m_props.metadata else None
                if owner and owner != current_user_id:
                    return func.HttpResponse("Forbidden.", status_code=403)
            transcript_name = (m_props.metadata or {}).get('transcript_blob_name') if m_props else None
            if not transcript_name:
                return func.HttpResponse("Cannot locate original transcript for regeneration.", status_code=400)

        if not transcript_name:
            return func.HttpResponse("Missing transcript_name.", status_code=400)

        # Load transcript JSON
        t_client = transcripts_blob_service.get_blob_client(TRANSCRIPTS_CONTAINER, transcript_name)
        if not t_client.exists():
            return func.HttpResponse("Transcript not found.", status_code=404)
        t_json = json.loads(t_client.download_blob().readall())
        transcript_text = t_json['combinedRecognizedPhrases'][0]['display']
        original_audio_url = t_json['source']

        # Load audio metadata (for user propagation and checks)
        parsed = urllib.parse.urlparse(original_audio_url)
        original_audio_blob_name = os.path.basename(parsed.path)
        a_client = audio_blob_service.get_blob_client(AUDIO_CONTAINER, original_audio_blob_name)
        a_props = a_client.get_blob_properties()
        audio_metadata = a_props.metadata or {}

        # Access check: if audio has owner metadata, ensure it matches
        if current_user_id and 'user_id' in audio_metadata and audio_metadata['user_id'] != current_user_id:
            return func.HttpResponse("Forbidden.", status_code=403)

        # Call Azure OpenAI
        openai_client = AzureOpenAI(
            azure_endpoint=os.environ.get("OPENAI_API_BASE"),
            api_key=os.environ.get("OPENAI_API_KEY"),
            api_version="2024-02-01"
        )

        system_prompt = "You are an expert assistant skilled at creating concise and accurate meeting minutes from a transcription. Structure the output clearly with headings like 'Summary', 'Action Items', and 'Decisions'."
        final_prompt = f"Please create meeting minutes based on the following transcription and user prompt.\n\nUser Prompt: {new_prompt}\n\nTranscription:\n\n{transcript_text}"

        ai_resp = openai_client.chat.completions.create(
            model=os.environ.get("OPENAI_DEPLOYMENT_NAME"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": final_prompt}
            ]
        )
        regenerated = ai_resp.choices[0].message.content

        # Save regenerated minutes (versioned filename)
        ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        audio_base, _ = os.path.splitext(original_audio_blob_name)
        if current_user_id:
            out_name = f"users/{current_user_id}/{audio_base}_minutes_{ts}.txt"
        else:
            out_name = f"{audio_base}_minutes_{ts}.txt"

        out_client = minutes_blob_service.get_blob_client(MINUTES_CONTAINER, out_name)
        minutes_metadata = {'transcript_blob_name': transcript_name}
        for k in ['user_id', 'user_details_b64', 'original_prompt_b64', 'original_filename_b64']:
            if k in audio_metadata:
                minutes_metadata[k] = audio_metadata[k]
        out_client.upload_blob(regenerated.encode('utf-8'), overwrite=True, metadata=minutes_metadata)

        return func.HttpResponse(
            json.dumps({
                'name': out_name,
                'minutes': regenerated
            }, ensure_ascii=False),
            mimetype="application/json",
            status_code=200
        )

    except Exception as e:
        logging.error(f"Error in regenerate-minutes: {e}", exc_info=True)
        return func.HttpResponse("Failed to regenerate minutes.", status_code=500)


@app.route(route="translate-minutes")
def translate_minutes(req: func.HttpRequest) -> func.HttpResponse:
    """Translate minutes text using Azure AI Translator.

    POST JSON:
      - name: minutes blob name to load and translate (optional if text provided)
      - text: raw text to translate (optional if name provided)
      - to: target language code (e.g., 'en', 'ja', 'zh-Hans')
      - from: source language code (optional; autodetect if omitted)
    """
    try:
        if req.method and req.method.upper() == 'GET':
            return func.HttpResponse("Use POST with JSON body.", status_code=405)

        body = req.get_json()
        if not isinstance(body, dict):
            return func.HttpResponse("Invalid JSON body.", status_code=400)

        minutes_name = body.get('name')
        source_text = body.get('text')
        to_lang = body.get('to')
        from_lang = body.get('from')
        save_flag = bool(body.get('save'))
        pre_translated = body.get('translated')  # 既に翻訳済みテキストを渡す場合
        if not to_lang:
            return func.HttpResponse("Missing 'to' language.", status_code=400)
        if not minutes_name and not source_text:
            return func.HttpResponse("Provide either 'name' or 'text'.", status_code=400)

        # Current user for access checks when loading by name
        auth_header = req.headers.get('X-MS-CLIENT-PRINCIPAL')
        current_user_id = None
        if auth_header:
            try:
                principal_json = base64.b64decode(auth_header).decode('utf-8')
                principal = json.loads(principal_json)
                current_user_id = principal.get('userId')
            except Exception as e:
                logging.warning(f"Failed to parse client principal in translate: {e}")

        # If name provided, load minutes text from storage with access checks
        minutes_props = None
        minutes_metadata = {}
        if minutes_name:
            minutes_connect_str = os.getenv('MINUTES_STORAGE_CONNECTION_STRING')
            if not minutes_connect_str:
                return func.HttpResponse("Server configuration error.", status_code=500)
            blob_service = BlobServiceClient.from_connection_string(minutes_connect_str)
            m_client = blob_service.get_blob_client(MINUTES_CONTAINER, minutes_name)
            if not m_client.exists():
                return func.HttpResponse("Minutes not found.", status_code=404)
            # Access check similar to get-minutes
            try:
                if current_user_id:
                    if minutes_name.startswith(f"users/{current_user_id}/") is False and minutes_name.startswith("users/"):
                        return func.HttpResponse("Forbidden.", status_code=403)
                    minutes_props = m_client.get_blob_properties()
                    minutes_metadata = minutes_props.metadata or {}
                    owner = minutes_metadata.get('user_id')
                    if owner and owner != current_user_id:
                        return func.HttpResponse("Forbidden.", status_code=403)
            except Exception as e:
                logging.warning(f"Access check failed in translate: {e}")
            # source_textが未指定の場合のみ取得
            if source_text is None:
                data = m_client.download_blob().readall()
                source_text = data.decode('utf-8', errors='replace') if isinstance(data, (bytes, bytearray)) else str(data)

        translated_text = None
        detected_lang = None
        if pre_translated and save_flag:
            # 既に翻訳済みテキストが渡されている場合は再翻訳せず保存だけ行う
            translated_text = str(pre_translated)
        else:
            # Call Azure Translator
            endpoint = os.getenv('TRANSLATOR_ENDPOINT')
            key = os.getenv('TRANSLATOR_KEY')
            region = os.getenv('TRANSLATOR_REGION')
            if not endpoint or not key:
                return func.HttpResponse("Translator not configured.", status_code=500)

            # Normalize endpoint
            # - Global Translator: https://api.cognitive.microsofttranslator.com -> /translate
            # - Cognitive Services resource: https://<res>.cognitiveservices.azure.com -> /translator/text/v3.0/translate
            endpoint = endpoint.rstrip('/')
            ep_lower = endpoint.lower()
            base = endpoint
            if '/translator/text/v3.0' in ep_lower:
                base = endpoint
            elif 'cognitiveservices.azure.com' in ep_lower:
                base = f"{endpoint}/translator/text/v3.0"
            else:
                base = endpoint  # assume global translator endpoint
            url = base if base.endswith('/translate') else f"{base}/translate"
            params = { 'api-version': '3.0', 'to': to_lang }
            if from_lang:
                params['from'] = from_lang
            headers = {
                'Ocp-Apim-Subscription-Key': key,
                'Content-Type': 'application/json'
            }
            if region:
                headers['Ocp-Apim-Subscription-Region'] = region

            payload = [{ 'text': source_text }]
            try:
                resp = requests.post(url, params=params, headers=headers, data=json.dumps(payload), timeout=30)
            except Exception as e:
                logging.error(f"Translator request failed: {e}")
                return func.HttpResponse("Translator request failed.", status_code=502)

            if resp.status_code < 200 or resp.status_code >= 300:
                logging.error(f"Translator error: {resp.status_code} {resp.text}")
                return func.HttpResponse(f"Translator error: {resp.text}", status_code=502)

            result = resp.json()
            try:
                if isinstance(result, list) and result:
                    item = result[0]
                    translations = item.get('translations') or []
                    if translations:
                        translated_text = translations[0].get('text')
                    det = item.get('detectedLanguage') or {}
                    detected_lang = det.get('language')
            except Exception:
                translated_text = None

        if translated_text is None:
            return func.HttpResponse("Failed to parse translation result.", status_code=500)

        # Save as new minutes if requested
        saved_name = None
        if save_flag:
            try:
                minutes_connect_str = os.getenv('MINUTES_STORAGE_CONNECTION_STRING')
                if not minutes_connect_str:
                    return func.HttpResponse("Server configuration error.", status_code=500)
                mbs = BlobServiceClient.from_connection_string(minutes_connect_str)
                # derive base name
                if minutes_name:
                    base = os.path.splitext(os.path.basename(minutes_name))[0]
                else:
                    base = 'minutes'
                ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
                prefix = ''
                owner_id = (minutes_metadata or {}).get('user_id')
                user_for_path = current_user_id or owner_id
                if user_for_path:
                    prefix = f"users/{user_for_path}/"
                saved_name = f"{prefix}{base}_translated_{to_lang}_{ts}.txt"
                out_client = mbs.get_blob_client(MINUTES_CONTAINER, saved_name)
                # build metadata (propagate + translation info)
                out_meta = {}
                for k in ['user_id', 'user_details_b64', 'original_prompt_b64', 'original_filename_b64', 'transcript_blob_name']:
                    if k in (minutes_metadata or {}):
                        out_meta[k] = minutes_metadata[k]
                out_meta['translated_from_name'] = minutes_name or ''
                out_meta['translated_to'] = to_lang
                if detected_lang:
                    out_meta['detected_language'] = detected_lang
                out_client.upload_blob((translated_text or '').encode('utf-8'), overwrite=True, metadata=out_meta)
            except Exception as e:
                logging.error(f"Failed to save translated minutes: {e}", exc_info=True)
                return func.HttpResponse("Failed to save translated minutes.", status_code=500)

        return func.HttpResponse(
            json.dumps({
                'to': to_lang,
                'detected': detected_lang,
                'translated': translated_text,
                'saved': bool(save_flag),
                'name': saved_name
            }, ensure_ascii=False),
            mimetype="application/json",
            status_code=200
        )

    except Exception as e:
        logging.error(f"Error in translate-minutes: {e}", exc_info=True)
        return func.HttpResponse("Failed to translate minutes.", status_code=500)


