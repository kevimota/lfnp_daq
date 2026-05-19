# ---- Base Stage ----
FROM node:24-slim AS base
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /app

# ---- Build Stage ----
FROM base AS build
# Copy only files needed for install
COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile

# Copy the rest and build
COPY . .
RUN pnpm build

# ---- Production Stage ----
FROM node:24-slim AS production
WORKDIR /app
ENV NODE_ENV=production

# Instead of copying node_modules and pruning, we copy 
# only the necessary files for a fresh production install.
COPY --from=build /app/package.json /app/pnpm-lock.yaml* ./
COPY --from=build /app/build ./build

# Install ONLY production dependencies here
RUN corepack enable && corepack prepare pnpm@latest --activate
RUN pnpm install --prod --frozen-lockfile --ignore-scripts

ENV HOST=0.0.0.0
ENV PORT=3000
EXPOSE 3000

CMD ["node", "build"]