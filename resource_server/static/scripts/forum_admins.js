document.addEventListener('DOMContentLoaded', () => {
    const forumID = document.querySelector('meta[name="forumID"]').getAttribute('value');
    const forumRibbon = document.querySelector('.forum-header .right');
    const forumPromptWindow = document.getElementById('forum-modal-del');
    const forumPromptWindowBox = document.getElementById('forum-modal-box-del');
    const forumTitleInput = document.getElementById('confirmation');
    const forumDeletionButton = document.getElementById('forum-delete');
    window.dependencyReady?.then(async () => {
        if (!localStorage.getItem('login')) {
            return;
        }

        try {
            const response = await fetch(`/forums/${forumID}/admins`, {
                method: 'GET',
                credentials: 'include'
            });

            const adminLevel = await response.json();
            console.info(adminLevel)
            if (adminLevel < 2) return;

            if (adminLevel === 3) {
                deleteForumButton = document.createElement('button');
                deleteForumButton.classList.add('btn-primary');
                deleteForumButton.innerText = 'Delete';
                deleteForumButton.addEventListener('click', () => {
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

                    forumDeletionButton.addEventListener('click', async () => {
                        try{
                            const response = await fetch(`/forums/${forumID}?redirect=1`, {
                                method:'DELETE',
                                credentials:'include',
                                body:JSON.stringify({confirmation:forumTitleInput.value.trim()}),
                                headers:{
                                    'Content-Type' : 'application/json'
                                }
                            });

                            if (!response.ok){
                                throw new Error('Failed to delete forum')
                            }

                            const data = response.json().getAttribute('redirect');
                            window.location.href = data;
                        }
                        catch(error){
                            console.error(error);
                        }
                    });
                });
                forumRibbon.appendChild(deleteForumButton)
            }

        }
        catch (error) {
            console.error(error);
        }
    });
});