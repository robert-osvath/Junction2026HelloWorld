import Shell from "../components/Shell";

export default function WorkspaceLayout({ children }: LayoutProps<"/">) {
  return <Shell>{children}</Shell>;
}
