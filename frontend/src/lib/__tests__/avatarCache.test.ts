/**
 * Session-scoped avatar cache: one fetch + one object URL per avatar URL per
 * page, shared by all <Avatar> instances; invalidation (current user's avatar
 * set/clear) forces exactly one refetch, and a stale in-flight load hands off
 * to the newest result instead of resurrecting an old image.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const { fetchAuthedBlob } = vi.hoisted(() => ({ fetchAuthedBlob: vi.fn() }))

vi.mock('../../api/endpoints', () => ({
  fetchAuthedBlob: (url: string) => fetchAuthedBlob(url),
}))

import {
  clearAvatarCache,
  getReadyAvatarUrl,
  getAvatarCacheVersion,
  invalidateAvatar,
  loadAvatar,
  subscribeAvatarCache,
} from '../avatarCache'

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void } {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function imageBlob(): Blob {
  return new Blob(['fake-image'], { type: 'image/png' })
}

beforeEach(() => {
  fetchAuthedBlob.mockReset()
  clearAvatarCache()
  let counter = 0
  URL.createObjectURL = vi.fn(() => `blob:mock-${++counter}`)
  URL.revokeObjectURL = vi.fn()
})

describe('loadAvatar', () => {
  it('fetches once per avatar URL and shares the object URL between concurrent callers', async () => {
    const d = deferred<Blob>()
    fetchAuthedBlob.mockReturnValue(d.promise)

    const p1 = loadAvatar('/api/users/1/avatar')
    const p2 = loadAvatar('/api/users/1/avatar')
    expect(fetchAuthedBlob).toHaveBeenCalledTimes(1)

    d.resolve(imageBlob())
    const [u1, u2] = await Promise.all([p1, p2])
    expect(u1).toBe(u2)
    expect(getReadyAvatarUrl('/api/users/1/avatar')).toBe(u1)

    // A later call hits the ready entry: no additional network work.
    await loadAvatar('/api/users/1/avatar')
    expect(fetchAuthedBlob).toHaveBeenCalledTimes(1)
  })

  it('loads different avatar URLs independently', async () => {
    const da = deferred<Blob>()
    const db = deferred<Blob>()
    fetchAuthedBlob
      .mockReturnValueOnce(da.promise)
      .mockReturnValueOnce(db.promise)

    const pa = loadAvatar('/api/users/1/avatar')
    const pb = loadAvatar('/api/users/2/avatar')
    expect(fetchAuthedBlob).toHaveBeenCalledTimes(2)

    da.resolve(imageBlob())
    db.resolve(imageBlob())
    const [ua, ub] = await Promise.all([pa, pb])
    expect(ua).not.toBe(ub)
  })

  it('a failed load drops the entry so a later call retries', async () => {
    const d1 = deferred<Blob>()
    fetchAuthedBlob.mockReturnValue(d1.promise)
    const p1 = loadAvatar('/api/users/1/avatar')
    d1.reject(new Error('boom'))
    await expect(p1).rejects.toThrow('boom')
    expect(getReadyAvatarUrl('/api/users/1/avatar')).toBeNull()

    const d2 = deferred<Blob>()
    fetchAuthedBlob.mockReturnValue(d2.promise)
    const p2 = loadAvatar('/api/users/1/avatar')
    expect(fetchAuthedBlob).toHaveBeenCalledTimes(2)
    d2.resolve(imageBlob())
    expect(await p2).toBeTruthy()
  })
})

describe('invalidateAvatar', () => {
  it('drops the entry; the next load refetches with a fresh object URL', async () => {
    const d1 = deferred<Blob>()
    fetchAuthedBlob.mockReturnValue(d1.promise)
    const p1 = loadAvatar('/api/users/1/avatar')
    d1.resolve(imageBlob())
    const u1 = await p1

    invalidateAvatar('/api/users/1/avatar')
    expect(getReadyAvatarUrl('/api/users/1/avatar')).toBeNull()

    const d2 = deferred<Blob>()
    fetchAuthedBlob.mockReturnValue(d2.promise)
    const p2 = loadAvatar('/api/users/1/avatar')
    expect(fetchAuthedBlob).toHaveBeenCalledTimes(2)
    d2.resolve(imageBlob())
    const u2 = await p2
    expect(u2).not.toBe(u1)
    expect(getReadyAvatarUrl('/api/users/1/avatar')).toBe(u2)
  })

  it('a stale in-flight load resolves to the newest object URL, not the old one', async () => {
    const d1 = deferred<Blob>()
    fetchAuthedBlob.mockReturnValue(d1.promise)
    const p1 = loadAvatar('/api/users/1/avatar')

    invalidateAvatar('/api/users/1/avatar')
    const d2 = deferred<Blob>()
    fetchAuthedBlob.mockReturnValue(d2.promise)
    const p2 = loadAvatar('/api/users/1/avatar')
    expect(fetchAuthedBlob).toHaveBeenCalledTimes(2)

    d2.resolve(imageBlob())
    const u2 = await p2
    // The stale load completes last: it must hand off to the new result.
    d1.resolve(imageBlob())
    const u1 = await p1
    expect(u1).toBe(u2)
  })

  it('notifies subscribers on load completion and invalidation', async () => {
    const seen: number[] = []
    const unsubscribe = subscribeAvatarCache(() => seen.push(getAvatarCacheVersion()))
    const start = getAvatarCacheVersion()

    const d = deferred<Blob>()
    fetchAuthedBlob.mockReturnValue(d.promise)
    void loadAvatar('/api/users/1/avatar')
    d.resolve(imageBlob())
    await vi.waitFor(() => expect(seen.length).toBe(1))

    invalidateAvatar('/api/users/1/avatar')
    expect(seen.length).toBe(2)
    unsubscribe()
    expect(start).toBe(0)
  })

  it('is a no-op for null/undefined URLs', () => {
    const v = getAvatarCacheVersion()
    invalidateAvatar(null)
    invalidateAvatar(undefined)
    expect(getAvatarCacheVersion()).toBe(v)
  })
})
