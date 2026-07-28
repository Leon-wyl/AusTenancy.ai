import type { TestingModule } from '@nestjs/testing';

declare global {
  namespace jest {
    interface Matchers<R> {
      toBeOk(): R;
    }
  }
}

expect.extend({
  toBeOk(received: unknown) {
    const pass =
      typeof received === 'object' &&
      received !== null &&
      'status' in received &&
      (received as Record<string, unknown>).status === 'ok';
    return {
      message: () => `expected ${JSON.stringify(received)} to have status "ok"`,
      pass,
    };
  },
});
