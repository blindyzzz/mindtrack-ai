# MindTrack AI

MindTrack AI is a lightweight student mental wellness web app built with Python's standard library and SQLite.

## Features

- Email/password sign up and login
- Personal dashboard for each user
- Daily mood check-in with optional notes
- Mood history and a simple visual trend view
- Mood-based daily suggestions
- AI support chat backed by Gemini when `GEMINI_API_KEY` is set
- Free built-in fallback support mode when Gemini is not configured
- Visible non-medical disclaimer throughout the app

## Run locally

```bash
python3 app.py
```

Then open `http://127.0.0.1:8000`.

## Gemini setup

Create an API key in Google AI Studio, then set it before starting the app:

```bash
export GEMINI_API_KEY="your_key_here"
export GEMINI_MODEL="gemini-2.5-flash"
python3 app.py
```

If no API key is set, the chat uses a safe built-in fallback support response.

You can copy settings from [.env.example](/Users/amiromorov/Documents/New%20project/.env.example) and follow the fuller deployment notes in [DEPLOY.md](/Users/amiromorov/Documents/New%20project/DEPLOY.md).

## Make It Public With Render

This repo includes [render.yaml](/Users/amiromorov/Documents/New%20project/render.yaml) for a simple public deploy.

1. Push this project to GitHub.
2. Create a new Web Service on [Render](https://render.com).
3. Connect your GitHub repo.
4. Add environment variables:

```bash
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODEL=gemini-1.5-flash
```

5. Deploy.

Render gives the app a public `onrender.com` URL. Note that SQLite is ephemeral on free web services, so this is best for demos and prototypes. For persistent public user data, move the database to Postgres.

## Data model

- `users`: email, password hash, created timestamp
- `sessions`: cookie session tokens
- `mood_entries`: date, mood, optional note
- `chat_messages`: user message, AI response, timestamp

## Notes

- The app stores data in `mindtrack.db`.
- Browser notifications are presented as a lightweight optional reminder section rather than a full scheduler.
