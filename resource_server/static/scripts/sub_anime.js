document.addEventListener('DOMContentLoaded', async () => {
    const subBtn = document.getElementById("anime-sub-btn");
    const pathParts = window.location.pathname.split('/');
    const animeId = pathParts[pathParts.length - 1];
    if (subBtn) {
        subBtn.addEventListener("click", async () => {
            const isAuthed = subBtn.value === "True";
            if (!isAuthed) {
                alert("Please log in to subscribe.");
                return;
            }
    
            const isCurrentlySubbed = subBtn.textContent.trim().toLowerCase().includes("unsubscribe");
            const url = `/animes/${animeId}/${isCurrentlySubbed ? "unsubscribe" : "subscribe"}`;
    
            try {
                const res = await fetch(url, {
                    method: "PATCH",
                    headers: {
                        "Content-Type": "application/json"
                    }
                });
    
                if (res.ok) {
                    subBtn.textContent = isCurrentlySubbed ? "Subscribe to this anime" : "Unsubscribe from this anime";
                } else {
                    const err = await res.json();
                    alert(err?.description || "Something went wrong while toggling subscription.");
                }
            } catch (err) {
                console.error("Subscription toggle error:", err);
                alert("An unexpected error occurred.");
            }
        });
    }
    
})