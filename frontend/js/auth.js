/* SAATHI - sending the signup form to the backend. */

const form = document.getElementById("signup-form");
const messageBox = document.getElementById("message");
const submitButton = document.getElementById("submit");

function showMessage(text, isError) {
  messageBox.textContent = text;
  messageBox.className = isError ? "message error" : "message success";
  messageBox.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();          // stop the browser from reloading the page
  messageBox.hidden = true;
  submitButton.disabled = true;

  try {
    const response = await fetch("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        saathi_id: document.getElementById("saathi-id").value,
        password: document.getElementById("password").value,
        confirm_password: document.getElementById("confirm-password").value,
      }),
    });

    const data = await response.json();

    if (response.ok) {
      showMessage(data.message + " Taking you to login...", false);
      setTimeout(() => { window.location.href = "/"; }, 1600);
      return;
    }

    // FastAPI sends a plain sentence for our own errors,
    // and a list for malformed requests.
    showMessage(
      typeof data.detail === "string" ? data.detail : "Please check your details.",
      true
    );
  } catch (error) {
    showMessage("Could not reach SAATHI. Is the server running?", true);
  }

  submitButton.disabled = false;
});
