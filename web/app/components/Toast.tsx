import Icon from "./Icon";

export default function Toast({ message }: { message: string }) {
  return (
    <div className="toast-region" role="status" aria-live="polite">
      {message && <div className="toast"><span><Icon name="check" /></span>{message}</div>}
    </div>
  );
}
