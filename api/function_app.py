import azure.functions as func
import logging

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# A minimal function to test Event Grid validation and basic logging.
@app.route(route="transcribe")
def transcribe_eventgrid_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python Event Grid trigger function processed a request for MINIMAL transcribe.')
    
    try:
        # Try to parse JSON, as Event Grid sends a JSON payload.
        events = req.get_json()
        logging.info(f"Received events: {events}")
        for event in events:
            # Handle validation specifically
            if event.get('eventType') == 'Microsoft.EventGrid.SubscriptionValidationEvent':
                validation_code = event['data']['validationCode']
                logging.info(f"Got validation event. Responding with code: {validation_code}")
                return func.HttpResponse(json.dumps({"validationResponse": validation_code}), mimetype="application/json")
    except Exception as e:
        # If parsing fails, it might be a simple GET request from a browser.
        logging.warning(f"Could not parse JSON, might be a browser test. Error: {e}")

    return func.HttpResponse("Hello from the minimal transcribe endpoint. This endpoint is active.", status_code=200)

# Add a second minimal function to ensure routing works.
@app.route(route="upload")
def minimal_upload_trigger(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('Python HTTP trigger function processed a request for MINIMAL upload.')
    return func.HttpResponse("Hello from the minimal upload endpoint.", status_code=200)
