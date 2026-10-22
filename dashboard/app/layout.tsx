import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FRIDAY | Crypto Research Control Room",
  description: "Research-only crypto structure, reversal and trade lifecycle monitor.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
