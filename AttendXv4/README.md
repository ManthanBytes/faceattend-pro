# FaceAttend — Updated Project

This package contains the FaceAttend Flask app and the updated web UI.

## Changes included

- Fixed refresh login restoration: the app now reads the actual object returned by `/api/users/<id>` instead of expecting an `{ok, user}` wrapper. It also keeps the current tab's saved session if the user-validation request temporarily fails, and clears it if the account is missing or no longer active.
- Added a **Delete** action to the Admin → Teachers table, with a confirmation prompt.
- Added a teacher-delete API route. It closes any active teacher sessions, records absent students for those closed sessions, removes the teacher account, and keeps historical session/attendance records for reports.
- Fixed the teacher list API so it returns active, pending, and rejected teachers plus their status and session count.
- Added a public, visible `/about` page with page-specific SEO metadata and SoftwareApplication structured data.
- Improved homepage title/description, canonical URL, Open Graph and Twitter metadata; retained the Google Search Console verification tag.
- Updated `robots.txt` to point to the sitemap and avoid crawling `/api/`; added `/about` to `sitemap.xml`.
- Updated the visible app branding to FaceAttend.

## Deploy to Vercel

1. Extract the ZIP and push the `AttendXv4` folder contents to your GitHub repository (keep the `vercel.json` at the deployed project root).
2. Deploy/redeploy the project on Vercel.
3. Check these URLs after deployment:
   - `https://faceattend-pro.vercel.app/`
   - `https://faceattend-pro.vercel.app/about`
   - `https://faceattend-pro.vercel.app/robots.txt`
   - `https://faceattend-pro.vercel.app/sitemap.xml`
4. In Google Search Console, open **Sitemaps**, submit `sitemap.xml` again, then use **URL inspection** for `/about` and choose **Request indexing**. You can also inspect the homepage. Google decides when/if URLs are indexed; requesting indexing does not guarantee a ranking or immediate appearance.

## Important production notes

- **Database persistence:** Vercel serverless deployments should use a persistent database configured with the `DATABASE_URL` environment variable. A local SQLite file can be ephemeral or unsuitable for writes across serverless instances. Back up any real attendance data before changing database configuration.
- **Database privacy:** `attendx.db` is included in this ZIP to preserve the supplied project's existing data. Do not commit a real database containing student/teacher details, face data, or password hashes to a public GitHub repository. Keep backups private. `.gitignore` now ignores local database files; if `attendx.db` was already tracked by Git, remove it from tracking with `git rm --cached AttendXv4/attendx.db` (adjust the path to your repository) before pushing, after confirming the deployed app uses a persistent database.
- **Security review:** This update adds the requested UI/API features but does not redesign the app's overall API authentication/authorization. Before using it with real institutional/student data, add server-side authentication and role checks to every sensitive API route, change any default/demo credentials, and configure secrets securely. A front-end-only role check is not a security boundary.
- **Face data:** Make sure your institution obtains appropriate consent and applies suitable access controls, retention rules, and privacy notices for any biometric data.

## Files

- `app.py` — Flask routes and database operations
- `templates/index.html` — main login/application UI
- `templates/landing.html` — public SEO/about page
- `attendx.db` — original supplied SQLite database, preserved as provided
- `requirements.txt` and `vercel.json` — deployment configuration
