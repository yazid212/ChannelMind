import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ChannelMind — Ask any YouTube channel",
  description: "Grounded AI answers from YouTube transcripts with precise timestamp citations.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
