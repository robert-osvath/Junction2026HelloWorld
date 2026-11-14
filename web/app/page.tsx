import Link from "next/link";

export default function Home() {
  return (
    <>
      <section className="hero">
        <p className="eyebrow">Open ordering interface</p>
        <h1>Let any buyer agent order from Aimo, <em>safely</em>.</h1>
        <p className="lede">
          External buyer agents place orders with Aimo. A jury of code gates and models decides, humans approve
          the rest, and every step lands in a verified audit trail.
        </p>
      </section>
      <div className="cards">
        <a className="card" href="/vimo/index.html"><h2>Order as a buyer</h2><p>Ask Vimo for what you need and watch the jury decide.</p></a>
        <Link className="card" href="/approvals"><h2>Approve as Aimo</h2><p>Review held orders, approve or reject, and check the audit log.</p></Link>
        <a className="card" href="/game/index.html"><h2>Watch the pitch</h2><p>The Milk Run: the story of one order, end to end.</p></a>
      </div>
      <p className="muted">Chrome/Edge recommended · microphone optional, text works too</p>
    </>
  );
}
