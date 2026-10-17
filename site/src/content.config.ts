import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";
import { z } from "astro/zod";

const docs = defineCollection({
  loader: glob({ pattern: "*.md", base: "../docs" }),
  schema: z.object({
    title: z.string(),
    description: z.string().optional(),
    weight: z.number().default(100),
  }),
});

export const collections = { docs };
