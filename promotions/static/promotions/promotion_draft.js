(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    const panel = document.getElementById('liner-draft-panel');
    const form = document.getElementById('promotion_form');
    const generateButton = document.getElementById('liner-generate-draft');
    const applyButton = document.getElementById('liner-apply-draft');
    const preview = document.getElementById('liner-draft-preview');
    const error = document.getElementById('liner-draft-error');
    const previewTitle = document.getElementById('liner-draft-title');
    const previewDescription = document.getElementById('liner-draft-description');

    if (!panel || !form || !generateButton || !applyButton) {
      return;
    }

    let currentDraft = null;
    let currentDraftInputSignature = null;
    const draftInputNames = new Set([
      'store',
      'benefit',
      'terms',
      'starts_at_0',
      'starts_at_1',
      'ends_at_0',
      'ends_at_1',
      'requires_coupon',
      'redeem_until_0',
      'redeem_until_1',
      'total_quantity',
      'ai_context',
    ]);

    function getDraftInputSignature() {
      const formData = new FormData(form);
      return JSON.stringify(
        Array.from(draftInputNames, function (name) {
          return [name, formData.getAll(name)];
        })
      );
    }

    function invalidateDraft() {
      currentDraft = null;
      currentDraftInputSignature = null;
      previewTitle.textContent = '';
      previewDescription.textContent = '';
      preview.hidden = true;
      applyButton.disabled = true;
    }

    function showError(message) {
      error.textContent = message;
      error.hidden = false;
    }

    function invalidateDraftForChangedInput(event) {
      if (event.target.name && draftInputNames.has(event.target.name)) {
        invalidateDraft();
      }
    }

    form.addEventListener('input', invalidateDraftForChangedInput);
    form.addEventListener('change', invalidateDraftForChangedInput);
    invalidateDraft();

    generateButton.addEventListener('click', async function () {
      invalidateDraft();
      generateButton.disabled = true;
      generateButton.textContent = '생성 중…';
      error.hidden = true;

      const formData = new FormData(form);
      const requestInputSignature = getDraftInputSignature();
      if (panel.dataset.objectId) {
        formData.set('object_id', panel.dataset.objectId);
      }

      try {
        const csrfInput = form.querySelector('input[name="csrfmiddlewaretoken"]');
        const response = await fetch(panel.dataset.endpoint, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {'X-CSRFToken': csrfInput ? csrfInput.value : ''},
          body: formData,
        });
        const data = await response.json();
        if (!response.ok) {
          let message = data.error && data.error.message
            ? data.error.message
            : '초안 생성에 실패했습니다.';
          if (data.error && data.error.details) {
            const detailMessages = Object.values(data.error.details).flat();
            if (detailMessages.length) {
              message += ' ' + detailMessages.join(' ');
            }
          }
          showError(message);
          return;
        }
        if (requestInputSignature !== getDraftInputSignature()) {
          showError('입력값이 변경되어 생성된 초안을 폐기했습니다. 다시 생성해 주세요.');
          return;
        }

        currentDraft = data.draft;
        currentDraftInputSignature = requestInputSignature;
        previewTitle.textContent = currentDraft.title;
        previewDescription.textContent = currentDraft.description;
        preview.hidden = false;
        applyButton.disabled = false;
      } catch (requestError) {
        showError('초안 생성 요청에 실패했습니다. 잠시 후 다시 시도해 주세요.');
      } finally {
        generateButton.disabled = false;
        generateButton.textContent = 'AI 문구 초안 생성';
      }
    });

    applyButton.addEventListener('click', function () {
      if (!currentDraft || currentDraftInputSignature !== getDraftInputSignature()) {
        invalidateDraft();
        showError('입력값이 변경되어 기존 초안을 적용할 수 없습니다. 다시 생성해 주세요.');
        return;
      }
      const titleInput = document.getElementById('id_title');
      const descriptionInput = document.getElementById('id_description');
      if (titleInput && descriptionInput) {
        titleInput.value = currentDraft.title;
        descriptionInput.value = currentDraft.description;
      }
    });
  });
}());
