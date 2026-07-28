## File Structure

## Development

### Prerequisites
- Node.js 18+
- npm or yarn

### Local setup (recommended: no Okta + no CSRF)

This app supports running locally without Okta redirects and without CSRF bootstrap logic.

1. Create a local env file:

```bash
cp .env.example .env.local
```

2. Set these values in `.env.local`:

```bash
# Backend base URL (WITHOUT /api)
VITE_API_BASE_URL=http://localhost:8000

# Local dev toggles
VITE_ENABLE_OKTA=false
VITE_ENABLE_CSRF=false
```

3. Install dependencies and start the dev server:

```bash
npm install
npm run dev
```

Notes:

- If your backend is running on a different port/host, update `VITE_API_BASE_URL`.
- When `VITE_ENABLE_OKTA=false`, the SPA will not use Okta and will not redirect to login.
- When `VITE_ENABLE_CSRF=false`, Axios will not call `/api/v1/user/get-user` for CSRF bootstrapping and will not attach CSRF headers.

### Installation
```bash
npm install
```

### Development Server
```bash
npm run dev
```

### Build
```bash
npm run build
```

## dependencies

- React 18 with TypeScript
- Vite
- Tailwind CSS
- React Router
- Chart.js
- Axios
- date-fns
- file-saver
- exceljs
- Lucide React Icons



