FROM node:20-slim AS build

WORKDIR /app
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_LOCAL_DEMO_AUTH=false
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_LOCAL_DEMO_AUTH=$VITE_LOCAL_DEMO_AUTH
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
USER nginx

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
