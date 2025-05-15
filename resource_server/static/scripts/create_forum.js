document.addEventListener('DOMContentLoaded', () => {
    const animeID = window.location.pathname.split("/")[3];
    const forumPromptWindow = document.getElementById('forum-modal');
    const forumPromptWindowBox = document.getElementById("forum-modal-box");
    const createForumButton = document.getElementById('forum-make-btn');

    window.dependencyReady?.then(async () => {
        if (!localStorage.getItem('login')) {
            createForumButton.innerText = 'Login to create a forum';
            createForumButton.addEventListener('click', () => {
                window.location.href = '/login';
            });
            return;
        }

        createForumButton.addEventListener('click', () => {
            forumPromptWindow.classList.remove('hidden');
            const outsideClickHandler = (event) => {
                if (!forumPromptWindowBox.contains(event.target)) {
                    forumPromptWindow.classList.add('hidden');
                    document.removeEventListener('click', outsideClickHandler);
                }
            };
            setTimeout(() => {

                document.addEventListener('click', outsideClickHandler);
            }, 0.1);

            const submitButton = document.getElementById('forum-submit');
            submitButton.addEventListener('click', async () => {
                try {
                    fname = document.getElementById('forum-name').value;
                    if (!fname || fname === undefined || fname.trim().length < 6) {
                        alert("Invalid forum name");
                        return;
                    }

                    fdesc = document.getElementById('forum-desc').value;

                    const response = await fetch('/forums', {
                        method: "POST",
                        credentials: 'include',
                        body: JSON.stringify({ forum_name: fname, desc: fdesc ? null : fdesc, anime_id: animeID }),
                        headers: {
                            'Content-Type': 'application/json'
                        }
                    });

                    if (!response.ok) {
                        throw new Error();
                    }

                    alert("Forum made!");
                    forumPromptWindow.classList.add('hidden');
                    document.removeEventListener('click', outsideClickHandler);

                }
                catch (error) {
                    console.error("Failed to create a forum");
                    forumPromptWindow.classList.remove('hidden');
                }
            })
        });
    });
});