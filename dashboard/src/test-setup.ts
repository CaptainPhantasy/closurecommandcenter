import "@testing-library/jest-dom/vitest"

const store = new Map<string, string>()

const localStorageMock: Storage = {
	getItem: (key) => store.get(key) ?? null,
	setItem: (key, value) => { store.set(key, String(value)) },
	removeItem: (key) => { store.delete(key) },
	clear: () => { store.clear() },
	get length() { return store.size },
	key: (i) => [...store.keys()][i] ?? null,
}

// Replace Node v25's built-in localStorage before any module loads.
// Zustand persist hydrates at store creation (import time),
// which runs before beforeAll/beforeEach hooks fire.
Object.defineProperty(globalThis, "localStorage", {
	value: localStorageMock,
	writable: true,
})

