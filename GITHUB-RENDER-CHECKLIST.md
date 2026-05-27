# GitHub and Render Checklist

## 1. Create a GitHub Repository

Suggested repository name:

`dentvoice-ai`

Suggested description:

`AI receptionist MVP for dental clinics with FastAPI, dashboard, and Twilio-compatible voice booking flow.`

Keep it public if you want recruiters and connections to see it.

## 2. Initialize Git Locally

Run these commands inside the project folder:

```bash
git init
git add .
git commit -m "Initial commit: DentVoice AI MVP"
```

## 3. Connect to GitHub

After you create the empty GitHub repository, run:

```bash
git remote add origin https://github.com/YOUR-USERNAME/dentvoice-ai.git
git branch -M main
git push -u origin main
```

## 4. Deploy on Render

In Render:

1. Click `New +`
2. Choose `Web Service`
3. Connect your GitHub account
4. Select the `dentvoice-ai` repository
5. Use these settings if Render does not auto-detect them:

```text
Runtime: Python
Build Command: pip install -r requirements.txt
Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Health check:

```text
/health
```

## 5. After Deployment

Open the Render URL and confirm:

- the dashboard loads
- `/health` returns status ok
- `/api/dashboard` works

## 6. Next Step After Render

Once the Render URL is live, use it for Twilio:

```text
POST https://YOUR-RENDER-URL/voice/incoming
```

## 7. What to Put on LinkedIn

Use:

- GitHub repository link
- Render live demo link
- short summary of what the product does
- what technologies you used
