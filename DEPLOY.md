# Deploy MindTrack AI

## Local Gemini run

1. Create a Gemini API key in Google AI Studio.
2. Start the app with:

```bash
cd "/Users/amiromorov/Documents/New project"
export GEMINI_API_KEY="your_key_here"
export GEMINI_MODEL="gemini-2.5-flash"
export GEMINI_FALLBACK_MODEL="gemini-2.5-flash-lite"
python3 app.py
```

3. Open `http://127.0.0.1:8000`.

If `GEMINI_API_KEY` is not set when the server starts, the app will use built-in fallback support responses instead of Gemini.

## Render deploy

1. Push this folder to a GitHub repository.
2. Sign in to Render.
3. Create a new `Web Service`.
4. Connect the GitHub repository.
5. Render will detect `render.yaml`.
6. Add these environment variables in Render:

```text
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODEL=gemini-2.5-flash-lite
```

7. Deploy and share the generated `onrender.com` URL.

## Important note

This app currently stores data in SQLite. That is acceptable for demos and early prototypes, but not ideal for durable public production data. Move to Postgres before treating it as a real production app.
