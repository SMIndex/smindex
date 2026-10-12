export interface WSOptions {
  maxRetries?: number;
  retryDelay?: number;
  onMessage?: (data: any) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

export class ReconnectingWebSocket {
  private url: string;
  private ws: WebSocket | null = null;
  private retryCount = 0;
  private maxRetries: number;
  private baseDelay: number;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionalClose = false;
  private onMessage: ((data: any) => void) | undefined;
  private onOpen: (() => void) | undefined;
  private onClose: (() => void) | undefined;

  constructor(url: string, options: WSOptions = {}) {
    this.url = url;
    // Default: never give up. A finite cap silently froze prices for the rest
    // of the session once exhausted (e.g. laptop sleep > ~17 min of backoff).
    this.maxRetries = options.maxRetries ?? Infinity;
    this.baseDelay = options.retryDelay ?? 1000;
    this.onMessage = options.onMessage;
    this.onOpen = options.onOpen;
    this.onClose = options.onClose;
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return;

    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.retryCount = 0;
      this.startHeartbeat();
      this.onOpen?.();
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'pong') return;
        this.onMessage?.(data);
      } catch {
        // ignore non-JSON messages
      }
    };

    this.ws.onclose = () => {
      this.stopHeartbeat();
      this.onClose?.();
      if (!this.intentionalClose) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  send(data: object): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  close(): void {
    this.intentionalClose = true;
    this.stopHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }

  get readyState(): number {
    return this.ws?.readyState ?? WebSocket.CLOSED;
  }

  private scheduleReconnect(): void {
    if (this.retryCount >= this.maxRetries) return;

    const delay = Math.min(
      this.baseDelay * Math.pow(2, this.retryCount),
      30000,
    );
    this.retryCount++;

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: 'ping' });
    }, 30000);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }
}
