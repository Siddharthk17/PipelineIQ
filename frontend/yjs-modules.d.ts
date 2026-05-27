declare module 'yjs' {
  export class AbstractType<EventType = unknown> {
    observe: (f: (event: EventType, transaction: Transaction) => void) => void
    unobserve: (f: (event: EventType, transaction: Transaction) => void) => void
    doc: Doc | null
    parent: AbstractType<unknown> | null
  }

  export class Doc {
    clientID: number
    getMap<T = unknown>(name?: string): YMap<T>
    getText(name?: string): YText
    getArray<T = unknown>(name?: string): YArray<T>
    transact: (f: (transaction: Transaction) => void, origin?: unknown) => void
    destroy: () => void
  }

  export class YMap<T = unknown> extends AbstractType<YMapEvent<T>> {
    get(key: string): T | undefined
    set(key: string, value: T): void
    delete(key: string): void
    clear(): void
    has(key: string): boolean
    size: number
    entries(): IterableIterator<[string, T]>
    values(): IterableIterator<T>
    keys(): IterableIterator<string>
    forEach(f: (value: T, key: string, map: YMap<T>) => void): void
    clone(): YMap<T>
    toJSON(): Record<string, T>
  }

  export class YText extends AbstractType<YTextEvent> {
    insert(index: number, text: string, attributes?: Record<string, unknown>): void
    delete(index: number, length: number): void
    toString(): string
    toDelta(): Array<{ insert?: string; delete?: number; retain?: number; attributes?: Record<string, unknown> }>
    length: number
  }

  export class YArray<T = unknown> extends AbstractType<YArrayEvent<T>> {
    get(index: number): T | undefined
    insert(index: number, content: T[]): void
    delete(index: number, length: number): void
    push(content: T[]): void
    slice(start?: number, end?: number): T[]
    length: number
  }

  export class UndoManager {
    constructor(typeScope: AbstractType<unknown> | AbstractType<unknown>[], options?: {
      captureTimeout?: number
      trackedOrigins?: Set<unknown>
    })
    undo(): void
    redo(): void
    clear(): void
  }

  export interface YEvent {
    target: AbstractType<unknown>
    currentTarget: AbstractType<unknown>
    transaction: Transaction
    path: Array<[AbstractType<unknown>, number] | [AbstractType<unknown>, string]>
  }

  export interface YMapEvent<T = unknown> extends YEvent {
    target: YMap<T>
    keysChanged: Set<string>
    changes: {
      keys: Map<string, { action: 'add' | 'update' | 'delete'; oldValue: T | undefined; newValue: T | undefined }>
    }
  }

  export interface YTextEvent extends YEvent {
    target: YText
    delta: Array<{ insert?: string; delete?: number; retain?: number; attributes?: Record<string, unknown> }>
  }

  export interface YArrayEvent<T = unknown> extends YEvent {
    target: YArray<T>
    delta: Array<{ insert?: T[]; delete?: number; retain?: number }>
  }

  export interface Transaction {
    doc: Doc
    origin: unknown
    local: boolean
    beforeState: Map<string, unknown>
    afterState: Map<string, unknown>
    changed: Map<AbstractType<unknown>, Set<string | number>>
    changedParentTypes: Set<AbstractType<unknown>>
  }

  export function encodeStateAsUpdate(doc: Doc, targetStateVector?: Uint8Array): Uint8Array
  export function applyUpdate(doc: Doc, update: Uint8Array, origin?: unknown): void
  export function encodeStateVector(doc: Doc): Uint8Array
}

declare module 'y-websocket' {
  import { Doc } from 'yjs'

  export class WebsocketProvider {
    awareness: {
      clientID: number
      getLocalState: () => Record<string, unknown> | null
      setLocalState: (state: Record<string, unknown> | null) => void
      getStates: () => Map<number, Record<string, unknown>>
      on: (event: string, callback: (changes: Array<{ added: number[]; updated: number[]; removed: number[] }>, source: 'local' | unknown) => void) => void
      off: (event: string, callback: (...args: unknown[]) => void) => void
    }
    wsconnected: boolean
    synced: boolean
    roomName: string

    constructor(
      serverUrl: string,
      roomName: string,
      doc: Doc,
      options?: {
        connect?: boolean
        params?: Record<string, string>
        awareness?: unknown
        maxBackoffTime?: number
        disableBc?: boolean
      }
    )

    connect(): void
    disconnect(): void
    destroy(): void
    on(event: string, callback: (event: unknown) => void): void
  }
}

declare module 'y-codemirror.next' {
  import { Extension } from '@codemirror/state'
  import type { YText } from 'yjs'

  export function yCollab(
    ytext: YText,
    awareness: unknown,
    options?: { undoManager?: unknown }
  ): Extension
}
