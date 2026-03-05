import type { Metadata } from 'next';
import localFont from 'next/font/local';
import { Providers } from './providers';
import './globals.css';

const inter = localFont({
  src: [
    { path: '../public/fonts/Inter-Regular.woff2', weight: '400', style: 'normal' },
    { path: '../public/fonts/Inter-Medium.woff2', weight: '500', style: 'normal' },
    { path: '../public/fonts/Inter-SemiBold.woff2', weight: '600', style: 'normal' },
    { path: '../public/fonts/Inter-Bold.woff2', weight: '700', style: 'normal' },
  ],
  variable: '--font-sans',
});

const jetbrainsMono = localFont({
  src: '../public/fonts/JetBrainsMono-Regular.woff2',
  variable: '--font-mono',
});

export const metadata: Metadata = {
  title: 'PipelineIQ',
  description: 'Data Pipeline Orchestration Engine',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable}`}>
      <body suppressHydrationWarning className="antialiased overflow-hidden">
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  );
}
