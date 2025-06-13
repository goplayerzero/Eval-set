package handlers

import (
	"database/sql"
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/george-e-shaw-iv/integration-tests-example/cmd/listd/item"
	"github.com/george-e-shaw-iv/integration-tests-example/internal/platform/web"
	"github.com/julienschmidt/httprouter"
	"github.com/pkg/errors"
)

// getItems is a handler that returns all rows from the item table.
func (a *Application) getItems(w http.ResponseWriter, r *http.Request) {
	listID, err := strconv.Atoi(httprouter.ParamsFromContext(r.Context()).ByName("lid"))
	if err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "convert list id to integer"))
		return
	}

	items, err := item.SelectItems(a.DB, listID)
	if err != nil {
		if errors.Cause(err) == sql.ErrNoRows {
			web.RespondError(w, r, http.StatusNotFound, errors.New(http.StatusText(http.StatusNotFound)))
			return
		}

		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "select all item rows"))
		return
	}

	if len(items) == 0 {
		items = make([]item.Item, 0)
	}

	web.Respond(w, r, http.StatusOK, items)
}

// getItems is a handler that creates a new row in the item table.
func (a *Application) createItem(w http.ResponseWriter, r *http.Request) {
	listID, err := strconv.Atoi(httprouter.ParamsFromContext(r.Context()).ByName("lid"))
	if err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "convert list id to integer"))
		return
	}

	var payload item.Item
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "unmarshal request payload"))
		return
	}

	payload.ListID = listID

	if payload.Name == "" {
		web.RespondError(w, r, http.StatusBadRequest, errors.New("name is a required field"))
		return
	}

	if payload.Quantity <= 0 {
		web.RespondError(w, r, http.StatusBadRequest, errors.New("quantity must be supplied and greater than 0"))
		return
	}

	i, err := item.CreateItem(a.DB, payload)
	if err != nil {
		if errors.Cause(err) == sql.ErrNoRows {
			web.RespondError(w, r, http.StatusNotFound, errors.New(http.StatusText(http.StatusNotFound)))
			return
		}

		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "insert row into item table"))
		return
	}

	web.Respond(w, r, http.StatusCreated, i)
}

// getItem is a handler that returns a row from the item table based off of the lid and iid URL
// parameters.
func (a *Application) getItem(w http.ResponseWriter, r *http.Request) {
	listID, err := strconv.Atoi(httprouter.ParamsFromContext(r.Context()).ByName("lid"))
	if err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "convert list id to integer"))
		return
	}

	itemID, err := strconv.Atoi(httprouter.ParamsFromContext(r.Context()).ByName("iid"))
	if err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "convert item id to integer"))
		return
	}

	i, err := item.SelectItem(a.DB, itemID, listID)
	if err != nil {
		if errors.Cause(err) == sql.ErrNoRows {
			web.RespondError(w, r, http.StatusNotFound, errors.New(http.StatusText(http.StatusNotFound)))
			return
		}

		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "select item by id and list id"))
		return
	}

	web.Respond(w, r, http.StatusOK, i)
}

// getItem is a handler that updates a row from the item table based off of the lid and iid URL
// parameters as well as a given payload.
func (a *Application) updateItem(w http.ResponseWriter, r *http.Request) {
	listID, err := strconv.Atoi(httprouter.ParamsFromContext(r.Context()).ByName("lid"))
	if err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "convert list id to integer"))
		return
	}

	itemID, err := strconv.Atoi(httprouter.ParamsFromContext(r.Context()).ByName("iid"))
	if err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "convert item id to integer"))
		return
	}

	var payload item.Item
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "unmarshal request payload"))
		return
	}

	payload.ID = itemID
	payload.ListID = listID

	if payload.Name == "" {
		web.RespondError(w, r, http.StatusBadRequest, errors.New("name is a required field"))
		return
	}

	if payload.Quantity <= 0 {
		web.RespondError(w, r, http.StatusBadRequest, errors.New("quantity must be supplied and greater than 0"))
		return
	}

	if err = item.UpdateItem(a.DB, payload); err != nil {
		if errors.Cause(err) == sql.ErrNoRows {
			web.RespondError(w, r, http.StatusNotFound, errors.New(http.StatusText(http.StatusNotFound)))
			return
		}

		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "update row in item table"))
		return
	}

	web.Respond(w, r, http.StatusOK, payload)
}

// getItem is a handler that deletes a row from the item table based off of the lid and iid URL
// parameters.
func (a *Application) deleteItem(w http.ResponseWriter, r *http.Request) {
	listID, err := strconv.Atoi(httprouter.ParamsFromContext(r.Context()).ByName("lid"))
	if err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "convert list id to integer"))
		return
	}

	itemID, err := strconv.Atoi(httprouter.ParamsFromContext(r.Context()).ByName("iid"))
	if err != nil {
		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "convert item id to integer"))
		return
	}

	if err = item.DeleteItem(a.DB, itemID, listID); err != nil {
		if errors.Cause(err) == sql.ErrNoRows {
			web.RespondError(w, r, http.StatusNotFound, errors.New(http.StatusText(http.StatusNotFound)))
			return
		}

		web.RespondError(w, r, http.StatusInternalServerError, errors.Wrap(err, "delete item row"))
		return
	}

	web.Respond(w, r, http.StatusNoContent, nil)
}
