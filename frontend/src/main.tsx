import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { CrestProvider } from "./crests";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <CrestProvider>
        <App />
      </CrestProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
