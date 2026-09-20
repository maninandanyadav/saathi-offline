/* SAATHI - sending the login form to the backend. */

const form = document.getElementById("login-form");
const messageBox = document.getElementById("message");
const submitButton = document.getElementById("submit");

function showMessage(text, isError) {
  messageBox.textContent = text;
  messageBox.className = isError ? "message error" : "message success";
  messageBox.hidden = false;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  messageBox.hidden = true;
  submitButton.disabled = true;

  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        saathi_id: document.getElementById("saathi-id").value,
        password: document.getElementById("password").value,
      }),
    });

    const data = await response.json();

    if (response.ok) {
      showMessage(data.message, false);
      window.location.href = "/home";
      return;
    }

    showMessage(
      typeof data.detail === "string" ? data.detail : "Please check your details.",
      true
    );
  } catch (error) {
    showMessage("Could not reach SAATHI. Is the server running?", true);
  }

  submitButton.disabled = false;
});
