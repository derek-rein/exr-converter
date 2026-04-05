/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./*.html'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['"SF Mono"', '"Fira Code"', '"JetBrains Mono"', 'monospace'],
        sans: ['"Helvetica Neue"', 'Arial', 'sans-serif'],
      },
      colors: {
        slate_bg: '#0a0a0f',
        slate_card: '#12121a',
        slate_border: '#1e1e2e',
        slate_accent: '#3b82f6',
        slate_muted: '#6b7280',
        slate_text: '#e2e8f0',
      },
      keyframes: {
        framePulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.7' },
        },
      },
      animation: {
        framePulse: 'framePulse 0.4s ease-in-out',
      },
    },
  },
  plugins: [],
};
