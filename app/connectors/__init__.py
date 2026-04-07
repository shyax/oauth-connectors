from app.connectors.google_drive import GoogleDriveConnector
from app.connectors.slack import SlackConnector


def get_connector(provider: str, integration, db):
    if provider == "google":
        return GoogleDriveConnector(integration, db)
    if provider == "slack":
        return SlackConnector(integration, db)
    raise ValueError(f"No connector registered for provider: {provider}")
