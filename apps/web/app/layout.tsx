import type { Metadata } from "next";
import type { ReactNode } from "react";

import { branding } from "@/config/branding";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: branding.productName,
  description: branding.description,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
