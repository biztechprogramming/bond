# Image Generation

When the user asks you to create, generate, or design any visual asset:

1. Use the `generate_image` tool with a detailed prompt
2. Be specific: describe style, colors, composition, mood, and content
3. For icons/logos: specify "minimal, clean, vector-style, solid background"
4. For UI mockups: describe layout, components, and color scheme
5. Always save to the workspace and show the user the result
6. If the first result isn't right, iterate — refine the prompt based on feedback

**Available providers:**
- **OpenAI** (DALL-E 3 / gpt-image-1) — best general quality
- **Replicate** (Flux, Stable Diffusion, 100+ models) — best variety
- **ComfyUI** (local) — free, private, requires local GPU

The tool automatically uses the user's configured provider. You can override with the `provider` parameter if needed.
