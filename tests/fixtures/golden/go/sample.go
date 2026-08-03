package sample

import (
	"context"
	"fmt"
)

// Identifiable is implemented by anything with an ID.
type Identifiable interface {
	GetID() string
}

// Status enumerates active states.
type Status struct {
	Label string
}

// BaseEntity is the base type.
type BaseEntity struct {
	name string
}

// User extends BaseEntity and implements Identifiable.
type User struct {
	BaseEntity
	id     string
	status Status
}

// GetID satisfies Identifiable.
func (u *User) GetID() string {
	return u.id
}

// Process returns a processed label.
func (u *User) Process(ctx context.Context) string {
	return u.helperCall(ctx, u.name)
}

// helperCall is a private helper.
func (u *User) helperCall(ctx context.Context, input string) string {
	fmt.Println(input)
	return "processed_" + input
}

// Build is a package-level function.
func Build(id string) *User {
	return &User{id: id}
}
