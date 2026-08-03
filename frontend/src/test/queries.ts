/** Small helpers the page tests share for asserting loading states. */

/** A promise that never settles, so a query stays pending for the assertion. */
export function pending<T>(): Promise<T> {
  return new Promise<T>(() => {});
}

/**
 * The spinners on the page.
 *
 * The ui-kit `Spinner` is a div wrapping a FontAwesome icon and carries no
 * role, so there is nothing to query it by except the icon it draws.
 */
export function spinners(): Element[] {
  return [...document.querySelectorAll('[data-icon="circle-notch"]')];
}
