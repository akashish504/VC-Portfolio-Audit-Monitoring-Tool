import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    exclude: ["lucide-react"],
    include: ["jodit-react"],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // Ensure the server listens on all interfaces (0.0.0.0) for Docker/container use
  server: {
    host: true, // This is the modern way to handle allowedHosts for Docker
    port: 3000, // Or your preferred development port
  },
  // If you must expose *some* environment variables to the browser at build time,
  // define them here, ensuring they are properly prefixed (VITE_...)
  // envPrefix: 'VITE_',
});

