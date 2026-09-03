"use client";

import dynamic from "next/dynamic";

const MapView = dynamic(() => import("./map"), { ssr: false });

export default function Home() {
  return (
    <div className="h-screen w-screen">
      <MapView />
    </div>
  );
}
