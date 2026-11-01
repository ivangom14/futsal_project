/** Logo de Futsito (SVG propio y provisional en `public/`; se sustituye cambiando esos dos archivos). */
export function LogoMark({ className = "h-9 w-9" }: { className?: string }) {
  return <img src="/futsito-mark.svg" alt="Futsito" className={className} width={128} height={128} />;
}

export function LogoFull({ className = "h-14 w-auto" }: { className?: string }) {
  return <img src="/futsito-logo.svg" alt="Futsito" className={className} width={420} height={128} />;
}
