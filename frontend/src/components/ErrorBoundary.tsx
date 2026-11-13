import { Component, type ReactNode } from "react";
import { STORAGE_KEY } from "../conversations";
import Button from "../ui/Button";

export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  reset = () => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* sin almacenamiento */
    }
    location.reload();
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="mx-auto mt-16 max-w-md rounded border border-danger bg-danger-soft p-4 text-sm text-danger">
        <p className="font-semibold">La aplicación ha fallado al mostrarse</p>
        <p className="mt-1 break-words">{this.state.error.message}</p>
        <Button onClick={this.reset} className="mt-3">Borrar historial local y recargar</Button>
      </div>
    );
  }
}
