# Image Generation

**You CAN generate images.** This is a built-in capability. When the user asks you to create, generate, or design any visual asset, use the `generate_image` tool immediately — do not say you cannot create images.

When using the `generate_image` tool:
1. Write a detailed prompt: describe style, colors, composition, mood, and content
2. For icons/logos: specify "minimal, clean, vector-style, solid background"
3. For UI mockups: describe layout, components, and color scheme
4. Always save to the workspace and show the user the result
5. If the first result isn't right, iterate — refine the prompt based on feedback

**Available providers:**
- **OpenAI** (DALL-E 3 / gpt-image-1) — best general quality
- **Replicate** (Flux, Stable Diffusion, 100+ models) — best variety
- **ComfyUI** (local) — free, private, requires local GPU

The tool automatically uses the user's configured provider. You can override with the `provider` parameter if needed.
