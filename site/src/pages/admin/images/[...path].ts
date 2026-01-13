import type { APIRoute } from "astro";
import fs from "node:fs";
import path from "node:path";

export const prerender = false;

const IMAGES_BASE = "/Users/jd/Projects/pda/cache/articles/ready/images";

// MIME types for common image formats
const MIME_TYPES: Record<string, string> = {
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".png": "image/png",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
};

export const GET: APIRoute = async ({ params }) => {
  // Check admin access
  if (!import.meta.env.ENABLE_ADMIN) {
    return new Response("Admin not enabled", { status: 403 });
  }

  const imagePath = params.path;
  if (!imagePath) {
    return new Response("No path provided", { status: 400 });
  }

  // Construct full path and validate it's within the images directory
  const fullPath = path.join(IMAGES_BASE, imagePath);
  const normalizedPath = path.normalize(fullPath);

  // Security: ensure path doesn't escape the images directory
  if (!normalizedPath.startsWith(IMAGES_BASE)) {
    return new Response("Invalid path", { status: 403 });
  }

  // Check if file exists
  if (!fs.existsSync(normalizedPath)) {
    return new Response("Image not found", { status: 404 });
  }

  // Read the file
  try {
    const fileBuffer = fs.readFileSync(normalizedPath);
    const ext = path.extname(normalizedPath).toLowerCase();
    const contentType = MIME_TYPES[ext] || "application/octet-stream";

    return new Response(fileBuffer, {
      status: 200,
      headers: {
        "Content-Type": contentType,
        "Cache-Control": "public, max-age=3600",
      },
    });
  } catch (error) {
    console.error("Error reading image:", error);
    return new Response("Error reading image", { status: 500 });
  }
};
