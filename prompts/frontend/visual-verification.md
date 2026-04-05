## Visual Verification Requirement

When making **any** change that affects the visual appearance of the Bond frontend — including
CSS, layout, styling, component structure, overflow, scrolling, sizing, spacing, or responsive
behavior — you **MUST** visually verify the change before committing:

1. Use the `visual-ui-test` skill to start the dev environment
2. Take a "before" screenshot of the affected page
3. Make your change
4. Take an "after" screenshot
5. Read both screenshots and confirm the change works as intended
6. If it doesn't look right, iterate until it does

**Do not** commit UI changes based solely on reading the code. Code that looks correct can
produce incorrect visual results due to CSS specificity, inherited styles, flex/grid layout
interactions, and overflow cascading.

**Do not** report a UI fix as complete without a screenshot proving it works.
