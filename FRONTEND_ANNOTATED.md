# RAG over Wikipedia — Frontend, Fully Annotated (every line commented)

> The rest of the React frontend — entry point, styles, build config, serving, and the frontend tests — with a comment on essentially every line. Comments in English.
> The four React components (`App.tsx`, `QueryBox`, `AnswerView`, `CitationList`) are already in **`ANNOTATED_CODE.md`**; this file covers everything else so there's no duplication.

## How the frontend fits together

```
index.html            ← the single HTML page the browser loads
   └─ loads /src/main.tsx        ← the JS entry point
        └─ renders <App/>        ← top-level component (in ANNOTATED_CODE.md)
             ├─ <QueryBox/>      ← the input + button
             ├─ <AnswerView/>    ← shows the answer
             └─ <CitationList/>  ← shows expandable sources
```

- **Dev:** `vite` serves the app with hot reload; a proxy forwards `/query` and `/health` to the API.
- **Build:** `tsc` type-checks, then `vite build` bundles everything into `dist/`.
- **Prod:** an `nginx` container serves `dist/` and proxies `/query` and `/health` to `${API_UPSTREAM}` (substituted at container startup; defaults to the Compose `api` service).
- **Tests:** `vitest` runs component tests in a fake DOM (`jsdom`).

---

# ENTRY & MARKUP

## `frontend/index.html` — the single HTML page

```html
<!doctype html>                                    <!-- HTML5 doctype -->
<html lang="en">                                   <!-- root element; language English -->
  <head>
    <meta charset="UTF-8" />                        <!-- character encoding -->
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />  <!-- responsive on mobile -->
    <title>RAG Wikipedia</title>                    <!-- browser tab title -->
  </head>
  <body>
    <div id="root"></div>                           <!-- empty container; React mounts the whole app here -->
    <script type="module" src="/src/main.tsx"></script>  <!-- load the JS entry point as an ES module -->
  </body>
</html>
```

## `frontend/src/main.tsx` — the JavaScript entry point

```tsx
import React from 'react'                            // React core (needed for <React.StrictMode>)
import ReactDOM from 'react-dom/client'              // the React 18 DOM renderer
import App from './App.tsx'                          // the top-level component
import './App.css'                                   // global styles (bundled by Vite)

ReactDOM.createRoot(document.getElementById('root')!).render(  // find <div id="root"> (! = "trust me, it exists") and create a React root
  <React.StrictMode>                                 // dev-only wrapper that surfaces potential bugs (double-invokes effects, etc.)
    <App />                                           // render the whole app
  </React.StrictMode>,
)
```

## `frontend/src/App.css` — minimal global styles

```css
.app {                                /* the outer wrapper div in App.tsx */
  max-width: 800px;                   /* cap the content width */
  margin: 0 auto;                     /* center it horizontally */
  padding: 2rem;                      /* breathing room around the content */
  font-family: system-ui, sans-serif; /* use the OS default UI font */
}

h1 {
  color: #333;                        /* dark grey heading */
}

.error {
  color: red;                         /* the error message (className="error" in App.tsx) is red */
}
```

---

# BUILD & TYPESCRIPT CONFIG

## `frontend/package.json` — dependencies and scripts

> Note: `package.json` must be **strict JSON** — the `//` comments below are for reading only; do **not** put them in the real file.

```jsonc
{
  "name": "rag-wikipedia-frontend",   // the package name
  "private": true,                    // never publish this to the npm registry
  "version": "0.1.0",
  "type": "module",                   // treat .js as ES modules (import/export), not CommonJS
  "scripts": {                        // run with `npm run <name>`
    "dev": "vite",                    // start the dev server with hot module reload
    "build": "tsc && vite build",     // 1) type-check with tsc, then 2) bundle to dist/ with Vite
    "preview": "vite preview",        // locally serve the built dist/ to preview production
    "test": "vitest run"              // run the test suite once (no watch mode)
  },
  "dependencies": {                   // shipped in the app bundle
    "react": "^18.3.1",               // the UI library
    "react-dom": "^18.3.1"            // renders React to the browser DOM
  },
  "devDependencies": {                // needed only to build/test, not shipped
    "@testing-library/jest-dom": "^6.4.2",    // extra DOM matchers (toBeInTheDocument, toHaveTextContent…)
    "@testing-library/react": "^16.0.0",      // render components inside tests
    "@testing-library/user-event": "^14.5.2", // simulate realistic user interactions
    "@types/react": "^18.3.3",                // TypeScript types for React
    "@types/react-dom": "^18.3.0",            // TypeScript types for react-dom
    "@vitejs/plugin-react": "^4.3.1",         // Vite plugin: JSX transform + fast refresh
    "jsdom": "^24.1.1",                       // a fake browser DOM so tests run in Node
    "typescript": "^5.2.2",                   // the type checker / compiler
    "vite": "^5.3.1",                         // the build tool + dev server
    "vitest": "^1.6.0"                        // the test runner (Vite-native, Jest-like API)
  }
}
```

## `frontend/vite.config.ts` — Vite build + dev-proxy + test config

```ts
import { defineConfig } from 'vite'                 // helper that gives type-checking on the config object
import react from '@vitejs/plugin-react'            // the React plugin (JSX, fast refresh)

export default defineConfig({
  plugins: [react()],                               // enable React support
  test: {                                           // Vitest configuration (lives here, not a separate file)
    environment: 'jsdom',                           // run tests against a fake DOM (no real browser)
    globals: true,                                  // expose test()/expect()/vi as globals (no imports needed)
    setupFiles: './src/test-setup.ts',              // run this file once before the test suite
  },
  server: {                                         // dev-server-only settings
    // `npm run dev` runs Vite on the HOST, where the Compose service name `api`
    // does NOT resolve — so the dev proxy targets localhost. `api:8000` belongs
    // only to nginx.conf.template, which runs inside the Compose network.
    proxy: {                                        // forward API calls to the backend during `npm run dev`
      '/query': process.env.VITE_DEV_PROXY_TARGET || 'http://localhost:8000',
      '/health': process.env.VITE_DEV_PROXY_TARGET || 'http://localhost:8000',
    },
  },
})
```

## `frontend/tsconfig.json` — TypeScript settings for the app (`src/`)

> Note: `tsconfig.json` **does** allow comments (TypeScript reads JSONC), so these can stay in the real file.

```jsonc
{
  "compilerOptions": {
    "target": "ES2020",                     // compile down to ES2020 JavaScript
    "useDefineForClassFields": true,        // standard class-field semantics
    "lib": ["ES2020", "DOM", "DOM.Iterable"], // available APIs: ES2020 language + the browser DOM
    "module": "ESNext",                     // emit modern ES module syntax
    "skipLibCheck": true,                   // don't type-check library .d.ts files (faster builds)
    "moduleResolution": "bundler",          // resolve imports the way Vite/bundlers do
    "allowImportingTsExtensions": true,     // permit `import App from './App.tsx'` (with the extension)
    "resolveJsonModule": true,              // allow importing .json files as modules
    "isolatedModules": true,                // each file must compile on its own (a Vite requirement)
    "noEmit": true,                         // tsc only type-checks; Vite produces the actual build
    "jsx": "react-jsx",                     // the new JSX transform (no `import React` needed per file)
    "strict": true,                         // turn on all strict type checks
    "noUnusedLocals": true,                 // error on unused local variables
    "noUnusedParameters": true,             // error on unused function parameters
    "noFallthroughCasesInSwitch": true,     // error on switch cases that fall through
    "types": ["vite/client", "vitest/globals"] // ambient types: import.meta.env + global test()/expect()
  },
  "include": ["src"],                       // type-check everything under src/
  "references": [{ "path": "./tsconfig.node.json" }] // link the Node-side config (for vite.config.ts)
}
```

## `frontend/tsconfig.node.json` — TypeScript settings for Node-side files (`vite.config.ts`)

```jsonc
{
  "compilerOptions": {
    "composite": true,                      // marks this as a referenced sub-project (enables project references)
    "skipLibCheck": true,                   // skip type-checking library files
    "module": "ESNext",                     // ES module output
    "moduleResolution": "bundler",          // bundler-style import resolution
    "allowSyntheticDefaultImports": true    // allow `import x from 'y'` even when y has no default export
  },
  "include": ["vite.config.ts"]             // this config governs only the Vite config file
}
```

---

# SERVING & CONTAINER

## `frontend/nginx.conf.template` — how the production container serves the app

It is a **template**, not a finished config: `${API_UPSTREAM}` is substituted **at
container startup**, not at build time. The `nginx:alpine` image automatically runs
`envsubst` over everything in `/etc/nginx/templates/` and writes the result into
`/etc/nginx/conf.d/`. That is what lets the same image point at the Compose `api`
service locally and at a different upstream in the cloud — **no rebuild required**.

```nginx
server {
    listen 80;                              # listen on port 80 inside the container
    root /usr/share/nginx/html;             # serve files from here (the built dist/ is copied in)
    index index.html;                       # default file

    location / {                            # any non-API path…
        try_files $uri $uri/ /index.html;   # …serve the file if it exists, else fall back to index.html (SPA routing)
    }

    location /query {                       # API calls to /query…
        proxy_pass ${API_UPSTREAM}/query;   # …forwarded to the upstream resolved at STARTUP
    }

    location /health {                      # and /health…
        proxy_pass ${API_UPSTREAM}/health;  # …forwarded to the backend too
    }
}
```

> **Two different upstreams, do not confuse them.** `api:8000` is a *Compose network*
> name and only resolves **inside** the container — it is the default baked in as
> `ENV API_UPSTREAM`. The Vite dev server runs on your **host**, where `api` does not
> resolve, so `vite.config.ts` proxies to `http://localhost:8000` instead.

## `frontend/Dockerfile` — two-stage build (Node builds, nginx serves)

```dockerfile
FROM node:20-alpine AS builder              # stage 1: a Node image to build the app
WORKDIR /app                                # working directory inside the build stage
COPY package.json .                         # copy the manifest first…
RUN npm install                             # …so this dependency layer is cached across source edits
COPY . .                                    # then copy the rest of the frontend source
# empty default keeps same-origin nginx proxying; Azure bakes the real API URL here at build time
ARG VITE_API_BASE_URL=""                    # build-time argument (Vite inlines VITE_* vars into the bundle)
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL    # expose it as an env var so `npm run build` can read it
RUN npm run build                           # type-check + bundle into /app/dist

FROM nginx:alpine                           # stage 2: a tiny nginx image to serve the built files
COPY --from=builder /app/dist /usr/share/nginx/html  # copy the built assets from stage 1
# install the TEMPLATE: nginx runs envsubst over /etc/nginx/templates/ at startup
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV API_UPSTREAM=http://api:8000            # default upstream = the Compose `api` service; override per environment
EXPOSE 80                                   # document that the container serves on port 80
```

---

# FRONTEND TESTS (Vitest)

## `frontend/src/test-setup.ts` — test bootstrap

```ts
import '@testing-library/jest-dom/vitest'   // registers the extra DOM matchers (toBeInTheDocument, toHaveTextContent…) with Vitest
```

## `frontend/src/components/QueryBox.test.tsx`

```tsx
import { fireEvent, render, screen } from '@testing-library/react'  // render a component + query/interact with it
import { vi } from 'vitest'                                          // vi.fn() = a spy/mock function
import QueryBox from './QueryBox'                                    // the component under test

test('renders input and button', () => {                            // the box shows its input and submit button
  render(<QueryBox onSubmit={vi.fn()} loading={false} />)           // render with a dummy onSubmit and not loading
  expect(screen.getByTestId('query-input')).toBeInTheDocument()     // the input exists in the DOM
  expect(screen.getByTestId('query-submit')).toBeInTheDocument()    // the button exists in the DOM
})

test('calls onSubmit with trimmed value', () => {                   // submitting trims whitespace and calls back
  const onSubmit = vi.fn()                                          // a spy so we can assert it was called
  render(<QueryBox onSubmit={onSubmit} loading={false} />)
  fireEvent.change(screen.getByTestId('query-input'), { target: { value: '  hello  ' } })  // type "  hello  "
  fireEvent.submit(screen.getByTestId('query-form'))               // submit the form
  expect(onSubmit).toHaveBeenCalledWith('hello')                   // callback received the trimmed value
})

test('disables input and button when loading', () => {             // during a request, the form is disabled
  render(<QueryBox onSubmit={vi.fn()} loading={true} />)           // render in the loading state
  expect(screen.getByTestId('query-input')).toBeDisabled()        // input greyed out
  expect(screen.getByTestId('query-submit')).toBeDisabled()       // button greyed out
})
```

## `frontend/src/components/AnswerView.test.tsx`

```tsx
import { render, screen } from '@testing-library/react'
import AnswerView from './AnswerView'                               // the component under test

test('displays answer text', () => {                                // it renders whatever answer it's given
  render(<AnswerView answer="Python is a language [1]." />)         // render with a sample answer
  expect(screen.getByTestId('answer-view')).toHaveTextContent('Python is a language')  // the text shows up
})
```

## `frontend/src/components/CitationList.test.tsx`

```tsx
import { fireEvent, render, screen } from '@testing-library/react'
import CitationList from './CitationList'                           // the component under test

const citations = [                                                 // two sample citations reused below
  { index: 1, title: 'Python', source_id: '1', excerpt: 'Python is a language.' },
  { index: 2, title: 'ML', source_id: '2', excerpt: 'ML is AI subset.' },
]

test('renders nothing when no citations', () => {                   // an empty list renders nothing (e.g. a refusal)
  const { container } = render(<CitationList citations={[]} />)
  expect(container).toBeEmptyDOMElement()                          // the component returned null → empty DOM
})

test('renders citation titles', () => {                             // each citation shows "[n] Title"
  render(<CitationList citations={citations} />)
  expect(screen.getByText(/\[1\] Python/)).toBeInTheDocument()      // "[1] Python" is shown (regex match)
  expect(screen.getByText(/\[2\] ML/)).toBeInTheDocument()         // "[2] ML" is shown
})

test('expands citation on click', () => {                           // clicking a citation reveals its excerpt
  render(<CitationList citations={citations} />)
  expect(screen.queryByTestId('citation-excerpt-1')).not.toBeInTheDocument()  // excerpt hidden initially
  fireEvent.click(screen.getByTestId('citation-toggle-1'))         // click to expand citation 1
  expect(screen.getByTestId('citation-excerpt-1')).toHaveTextContent('Python is a language.')  // excerpt now visible
})
```

---

# HOW TO RUN

```bash
cd frontend
npm install          # install dependencies (only needed once)
npm run dev          # dev server with hot reload (proxies /query to the api container)
npm run build        # type-check (tsc) + bundle to dist/
npm run test         # run the Vitest component tests
# in Docker: the whole thing builds + serves via docker-compose (see the frontend service)
```

## What to say about the frontend in an interview

- **"It's a deliberately thin UI"** — the intelligence is in the backend; the frontend just calls `POST /query` and renders the `{answer, citations[]}` shape.
- **"The API base URL is configurable at two layers"** — in the **bundle** at build time via `VITE_API_BASE_URL` (empty by default, so the app calls same-origin paths and any trailing slash is stripped before `/query` is appended), and at the **proxy** at container start via `${API_UPSTREAM}` in `nginx.conf.template`. Locally, `npm run dev` uses **Vite's own proxy** — nginx is only in the production container.
- **"Two-stage Docker build"** — Node builds the static bundle, then a tiny nginx image serves it and proxies API calls, so the runtime image ships no Node.
- **"Components are tested in a fake DOM"** — Vitest + jsdom + Testing Library verify rendering, the trimmed-submit behaviour, the loading/disabled state, and that a citation **expands** on click. (Collapse is not asserted — the tests click once and check the excerpt appears.)
