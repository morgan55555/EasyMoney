# --- Build stage: compile the SPA with Vite ---
FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
# The SPA, then the unhashed Web Push service worker (dist/sw.js, AGENTS.md §2.14).
RUN npm run build && npm run build:sw

# --- Runtime stage: serve static assets + proxy API/WS to the backend ---
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
