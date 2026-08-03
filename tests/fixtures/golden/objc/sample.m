#import <Foundation/Foundation.h>

@protocol Identifiable <NSObject>
@property (nonatomic, copy) NSString *id;
@end

typedef NS_ENUM(NSInteger, Status) {
    StatusActive,
    StatusInactive
};

@interface BaseEntity : NSObject
@property (nonatomic, copy) NSString *name;
- (instancetype)initWithName:(NSString *)name;
@end

@implementation BaseEntity
- (instancetype)initWithName:(NSString *)name {
    self = [super init];
    if (self) {
        _name = [name copy];
    }
    return self;
}
@end

@interface User : BaseEntity <Identifiable>
@property (nonatomic, copy) NSString *id;
@property (nonatomic, assign) Status status;
- (instancetype)initWithId:(NSString *)id name:(NSString *)name;
- (NSString *)process;
@end

@implementation User
- (instancetype)initWithId:(NSString *)id name:(NSString *)name {
    self = [super initWithName:name];
    if (self) {
        _id = [id copy];
        _status = StatusActive;
    }
    return self;
}

- (NSString *)process {
    return [self helperCall:self.name];
}

- (NSString *)helperCall:(NSString *)input {
    return [NSString stringWithFormat:@"processed_%@", input];
}
@end
