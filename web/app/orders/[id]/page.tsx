import { Suspense } from "react";
import OrderView from "./Order";

async function Loaded({ params }: { params: PageProps<"/orders/[id]">["params"] }) {
  const { id } = await params;
  return <OrderView id={id} />;
}

export default function Page({ params }: PageProps<"/orders/[id]">) {
  return <Suspense fallback={<p>Loading...</p>}><Loaded params={params} /></Suspense>;
}
