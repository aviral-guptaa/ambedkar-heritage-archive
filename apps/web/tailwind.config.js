/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 50: '#f7f7f5', 100: '#eceae4', 200: '#d6d2c7', 400: '#8a8272', 600: '#5b5445', 800: '#33302a', 900: '#1c1b18' },
        saffron: { 50: '#fff8ec', 100: '#ffefd2', 300: '#f7c469', 500: '#dd8f1c', 700: '#9a5c0b' },
        indigo: { 50: '#eef1fb', 100: '#dbe1f7', 500: '#3b4d8f', 700: '#26315c', 900: '#161c36' },
      },
      fontFamily: {
        serif: ['Iowan Old Style', 'Palatino', 'Georgia', 'serif'],
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
