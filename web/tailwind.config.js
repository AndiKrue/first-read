/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#07090c',
        paper: '#f1eee7',
        signal: '#d3ff79',
        muted: 'var(--fr-text-muted)',
        subtle: 'var(--fr-text-subtle)',
      },
      fontFamily: {
        sans: ['Inter', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'monospace'],
      },
    },
  },
  plugins: [],
}
