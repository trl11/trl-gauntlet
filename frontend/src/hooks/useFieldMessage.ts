import { useEffect } from "react";

/**
 * Show a field's validation message as soon as the app sets one.
 *
 * The kit's `Input` and `Select` take an `error` prop and hand it to the
 * field's `setCustomValidity`, but they only repaint the message they show
 * from their own `blur` and `invalid` listeners. An error the app works out
 * while someone is typing therefore sets the field invalid and stays
 * invisible, and an error that clears leaves the old message on screen, until
 * the field is left. Both are wrong for a form that validates as it is filled
 * in.
 *
 * Dispatching `blur` is what makes the kit read the field's validity again.
 * The event alone does not move focus, and it repaints in both directions,
 * because the listener sets the message from whatever `validationMessage`
 * says at the time.
 *
 * Remove this once the kit paints a supplied `error` on its own. The kit is a
 * submodule and is not edited here.
 */
export function useFieldMessage(id: string, error?: string): void {
  useEffect(() => {
    document.getElementById(id)?.dispatchEvent(new Event("blur"));
  }, [error, id]);
}

export default useFieldMessage;
