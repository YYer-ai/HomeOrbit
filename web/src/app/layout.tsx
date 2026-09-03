import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HomeOrbit 家环",
  description: "AI + GIS 对话式住房选址服务",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="zh-CN"
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
