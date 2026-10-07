// Portal target for popups: inside shell B it is the bridged #dsb-portal node
// (B palette + theme); under shell A, or before the shell mounts, document.body.
export function portalRoot(): HTMLElement {
  return (typeof document !== 'undefined' && document.getElementById('dsb-portal')) || document.body;
}
