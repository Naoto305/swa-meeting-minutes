import os
import json
import base64
import logging
import tempfile
import subprocess
import urllib.parse
from datetime import datetime
from typing import Tuple, Union

from azure.storage.queue import QueueClient
from azure.storage.blob import BlobServiceClient, ContentSettings


def get_env(name: str, default: str = None, required: bool = False) -> str:
    val = os.getenv(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def parse_event_to_blob(url_or_event: Union[str, dict]) -> Tuple[str, str]:
    """Return (container, blob_name) from Event Grid event or URL string."""
    if isinstance(url_or_event, dict):
        # Storage BlobCreated event
        data = url_or_event.get('data') or {}
        url = data.get('url') or url_or_event.get('url')
    else:
        url = str(url_or_event)
    if not url:
        raise ValueError("No blob URL found in message")
    p = urllib.parse.urlparse(url)
    # path like /<container>/<blob...>
    path = p.path.lstrip('/')
    parts = path.split('/', 1)
    if len(parts) != 2:
        raise ValueError(f"Unexpected blob path: {path}")
    return parts[0], parts[1]


def decode_message_content(content: str) -> dict:
    # content is usually plain JSON text; but try base64->json as fallback
    try:
        return json.loads(content)
    except Exception:
        try:
            txt = base64.b64decode(content).decode('utf-8')
            return json.loads(txt)
        except Exception:
            raise


def derive_output_blob_name(src_blob_name: str) -> str:
    # Preserve users/{id}/ prefix if present; append _audio.wav
    prefix = ''
    file_part = src_blob_name
    if src_blob_name.startswith('users/'):
        parts = src_blob_name.split('/')
        if len(parts) >= 3:
            prefix = '/'.join(parts[:2]) + '/'
            file_part = parts[-1]
    base, _ = os.path.splitext(os.path.basename(file_part))
    return f"{prefix}{base}_audio.wav"


def main():
    logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s %(message)s')

    storage_conn = get_env('STORAGE_CONN', required=True)
    video_container = get_env('VIDEO_CONTAINER', 'video')
    audio_container = get_env('AUDIO_CONTAINER', 'audio')
    queue_name = get_env('QUEUE_NAME', 'q-video-extract')

    queue = QueueClient.from_connection_string(storage_conn, queue_name)
    messages = queue.receive_messages(visibility_timeout=60 * 10)  # 10 minutes lock
    msg = None
    try:
        msg = next(messages)
    except StopIteration:
        logging.info('No messages to process. Exiting.')
        return

    logging.info('Message received')
    try:
        payload = decode_message_content(msg.content)
    except Exception:
        logging.exception('Failed to decode queue message')
        # release message by not deleting; it will become visible again
        return

    try:
        container, blob_name = parse_event_to_blob(payload)
    except Exception:
        logging.exception('Failed to parse blob reference from message')
        return

    if container != video_container:
        logging.info(f'Skipping non-video container: {container}')
        queue.delete_message(msg)
        return

    blob_service = BlobServiceClient.from_connection_string(storage_conn)
    src = blob_service.get_blob_client(container=container, blob=blob_name)
    if not src.exists():
        logging.error('Source blob not found; deleting message')
        queue.delete_message(msg)
        return

    # Prepare temp files
    with tempfile.TemporaryDirectory() as td:
        in_path = os.path.join(td, os.path.basename(blob_name))
        out_path = os.path.join(td, 'out_audio.wav')

        logging.info(f'Downloading video: {blob_name}')
        with open(in_path, 'wb') as f:
            f.write(src.download_blob().readall())

        # Extract audio via ffmpeg: mono/16k WAV
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'info',
            '-i', in_path,
            '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav', out_path
        ]
        logging.info('Running ffmpeg to extract audio')
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            logging.error(f'ffmpeg failed with code {e.returncode}')
            return

        # Upload to audio container
        out_blob_name = derive_output_blob_name(blob_name)
        dst = blob_service.get_blob_client(container=audio_container, blob=out_blob_name)
        logging.info(f'Uploading extracted audio: {audio_container}/{out_blob_name}')

        # propagate metadata (ASCII only)
        meta = {}
        try:
            props = src.get_blob_properties()
            meta = props.metadata or {}
        except Exception:
            meta = {}

        # Ensure downstream functions have required metadata
        try:
            if 'original_prompt_b64' not in meta:
                default_prompt = '議事録を日本語で要約してください。'
                meta['original_prompt_b64'] = base64.b64encode(default_prompt.encode('utf-8')).decode('ascii')
            if 'original_filename_b64' not in meta:
                meta['original_filename_b64'] = base64.b64encode(os.path.basename(blob_name).encode('utf-8')).decode('ascii')
            if 'user_id' not in meta and blob_name.startswith('users/'):
                parts = blob_name.split('/')
                if len(parts) >= 2 and parts[1]:
                    meta['user_id'] = parts[1]
        except Exception:
            # If metadata enrichment fails, continue with whatever we have
            pass

        with open(out_path, 'rb') as f:
            dst.upload_blob(
                f,
                overwrite=True,
                content_settings=ContentSettings(content_type='audio/wav'),
                metadata=meta
            )

    # delete message on success
    queue.delete_message(msg)
    logging.info('Done.')


if __name__ == '__main__':
    main()
