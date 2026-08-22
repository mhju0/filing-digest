document.querySelectorAll("[data-demo-image]").forEach((button) => {
  const image = button.previousElementSibling?.querySelector("img");
  const animatedSource = button.dataset.animatedSrc;
  const staticSource = button.dataset.staticSrc;
  const label = button.textContent.trim().replace(/^Play /, "");

  if (!image || !animatedSource || !staticSource) return;

  button.addEventListener("click", () => {
    const isPlaying = button.getAttribute("aria-pressed") === "true";
    image.src = isPlaying ? staticSource : animatedSource;
    button.setAttribute("aria-pressed", String(!isPlaying));
    button.textContent = `${isPlaying ? "Play" : "Pause"} ${label}`;
  });
});
