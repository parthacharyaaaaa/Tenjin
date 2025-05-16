document.addEventListener('DOMContentLoaded', () => {
    const reportButton = document.getElementById('report-btn');
    const postID = window.location.pathname.split("/")[3];
    const postPromptWindow = document.getElementById('post-modal');
    const postPromptWindowBox = document.getElementById("post-modal-box");
    window.dependencyReady?.then(() => {
        if (!localStorage.getItem('login')) {
            reportButton.addEventListener('click', async () => {
                alert('Please login to report this post');
                window.location.href = '/login';
            });
            return;
        }
        reportButton.addEventListener('click', async () => {
            postPromptWindow.classList.remove('hidden');
            const outsideClickHandler = (event) => {
                if (!postPromptWindowBox.contains(event.target)) {
                    postPromptWindow.classList.add('hidden');
                    document.removeEventListener('click', outsideClickHandler);
                }
            };
            setTimeout(() => {
                document.addEventListener('click', outsideClickHandler);
            }, 0.1);

            const reportBtn = document.getElementById('report-submit');
            const reportReasonDropdown = document.getElementById('report-reason');
            const reportDescripton = document.getElementById('desc');

            reportBtn.addEventListener('click', async () => {
                const reportReason = reportReasonDropdown.value;
                const reportDesc = reportDescripton.value;

                if (!reportDesc || reportDesc === undefined || reportDesc.trim() === '') {
                    alert("Please add an informative description for your report");
                    return;
                }

                if(!reportReason || reportReason === undefined || reportReason.trim() === ''){
                    alert('Please provide a valid reason for reporting this post');
                    return;
                }

                const reportBody = JSON.stringify({ tag: reportReason, desc: reportDesc });

                try {
                    const response = await fetch(`/posts/${postID}/report`, {
                        method: 'PATCH',
                        credentials: 'include',
                        body: reportBody,
                        headers : {
                            'Content-Type' : 'application/json'
                        }
                    });

                    if (response.status === 409) {
                        alert('You have already reported this post');
                        postPromptWindow.classList.add('hidden');
                        document.removeEventListener('click', outsideClickHandler);
                        return;
                    }

                    if (!response.ok) {
                        throw new Error('Failed to report this post')
                    }

                    const data = response.json();
                    alert(data.message);
                    postPromptWindow.classList.add('hidden');
                    document.removeEventListener('click', outsideClickHandler);
                    return;
                }
                catch (error) {
                    console.error(error);
                    postPromptWindow.classList.add('hidden');
                    document.removeEventListener('click', outsideClickHandler);
                }
            })
        });
    });
});