# GMail OAuth 2.0 Handler

Minimal OAuth flow implementation for obtaining GMail refresh tokens.
Stores credentials in HACM for secure persistence.

## Installation

```bash
# Install dependencies
pip install requests memogarden-hacm

# Or use Poetry
poetry add requests memogarden-hacm
```

## Credential Setup

Client credentials and refresh tokens are seeded via the gmail-importer CLI
(this module provides the underlying `GmailOAuthClient` and callback server):

```bash
cd providers/gmail-importer
poetry run python -m gmail_importer.ingest --account you@gmail.com auth-setup   # client creds -> HACM
poetry run python -m gmail_importer.ingest --account you@gmail.com auth-login   # browser consent -> refresh token
```

The client secret is supplied via `--client-secrets FILE` (flat or GCP
Console download JSON) or an interactive prompt — never committed to the
repository. The callback flow uses the loopback redirect
`http://localhost:8080/callback`; the old OOB flow was shut down by Google
in 2023.

## API Endpoints (Flask)

### GET /api/v1/oauth/gmail/auth-url

Generate OAuth authorization URL.

**Query Parameters:**
- `hacm`: HACM credential file path (optional)
- `state`: Optional state parameter
- `redirect_uri`: Optional redirect URI override

**Response:**
```json
{
  "authorization_url": "https://accounts.google.com/o/oauth2/auth?...",
  "state": "random_state_token",
  "redirect_uri": "http://localhost:8080/callback"
}
```

### POST /api/v1/oauth/gmail/callback

Handle OAuth callback and store tokens.

**Request Body:**
```json
{
  "code": "authorization_code_from_callback",
  "state": "state_parameter",
  "account_id": "user@gmail.com"
}
```

**Response:**
```json
{
  "success": true,
  "account_id": "user@gmail.com",
  "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]
}
```

### GET /api/v1/oauth/gmail/credentials

List all stored GMail OAuth credentials.

**Response:**
```json
{
  "credentials": [
    {
      "account_id": "user@gmail.com",
      "provider": "google",
      "type": "oauth_refresh"
    }
  ]
}
```

## Usage Example

```python
from gmail_oauth import GmailOAuthClient

client = GmailOAuthClient("/var/lib/hacm/credentials.enc")

# 1. Get authorization URL
url = client.get_authorization_url()
print(f"Visit: {url}")

# 2. After user authorizes, exchange code for tokens
tokens = client.exchange_code_for_tokens(code, state)

# 3. Store refresh token in HACM
client.store_tokens("user@gmail.com", tokens)

# 4. Later: refresh access token
new_tokens = client.refresh_access_token("user@gmail.com")
```

## Security Notes

- **Never log access tokens** - Only log refresh token storage operations
- **HTTPS required** - Authorization URL must use HTTPS
- **State parameter** - Always use state parameter for CSRF protection
- **Client credentials** - Stored securely in HACM, never in logs
- **Redirect URI** - Must match what's registered in Google Cloud Console

## Setup Requirements

1. **Google Cloud Console:**
   - Create OAuth 2.0 client ID
   - Set authorized redirect URIs (e.g., `http://localhost:8080/callback`)
   - Enable Gmail API

2. **Client Credentials Storage:**
   Do not hardcode or commit client credentials. Seed them into HACM:
   ```bash
   cd providers/gmail-importer
   poetry run python -m gmail_importer.ingest --account you@gmail.com auth-setup --client-secrets /path/to/downloaded.json
   ```

